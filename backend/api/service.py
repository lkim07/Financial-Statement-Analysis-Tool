"""질문 하나 -> 답변 + 출처 + 하이라이트. FastAPI와 분리해서 CLI·테스트에서도 쓴다 (decision 030).

파이프라인은 Phase 7 그대로: 라우터(cascade) -> 검색(+브리지 분해) -> 생성 -> 코드 검사.
여기서 더하는 것은 화면용 정보뿐: 인용된 출처마다 라벨과 PDF 사각형.
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "rag"), str(ROOT / "ingest"), str(ROOT / "numbers")]
from highlights import computed_terms, locate, search_terms  # noqa: E402
from summary import kpis, pdf_text, waterfall  # noqa: E402

FIELDS = ["text", "page", "heading", "locations", "doc_id", "doc_label"]


@dataclass
class Deps:
    """서버가 시작할 때 한 번 만드는 것들 (모델 로딩은 몇 초 걸린다)."""
    embedder: object
    milvus: object
    llm: object
    model: str
    router_model: str
    collection: str
    registry: list[dict]
    pdfs: dict = field(default_factory=dict)  # doc_id -> 열린 PyMuPDF 문서 (처음 쓸 때 연다)

    def pdf(self, doc_id: str):
        if doc_id not in self.pdfs:
            import pymupdf as fitz
            from registry import get_doc, pdf_path
            self.pdfs[doc_id] = fitz.open(pdf_path(get_doc(doc_id)))
        return self.pdfs[doc_id]


def as_locations(value) -> list[dict]:
    return json.loads(value) if isinstance(value, str) else (value or [])


def source_view(chunk: dict, sentence_text: str, deps: Deps) -> dict:
    locations = as_locations(chunk.get("locations"))
    return {
        "kind": "chunk",
        "doc_id": chunk["doc_id"],
        "label": f"{chunk['doc_label']} p{chunk['page']}",
        "page": chunk["page"],
        "heading": chunk.get("heading") or "",
        "highlights": locate(deps.pdf(chunk["doc_id"]), locations, search_terms(sentence_text)),
    }


def computed_view(computed: dict, deps: Deps) -> dict:
    """계산 출처: 텍스트를 보여 주고, 계산에 쓴 재무제표 행을 칠한다."""
    rows = deps.milvus.get(deps.collection, ids=computed["source_chunks"], output_fields=FIELDS)
    terms = computed_terms(computed["text"])
    highlights = []
    for r in rows:
        highlights += [h for h in locate(deps.pdf(r["doc_id"]), as_locations(r["locations"]), terms, max_hits=20)
                       if h["level"] == "exact"]
    return {"kind": "computed", "doc_id": computed["doc_id"], "label": computed["doc_label"],
            "page": computed["page"], "heading": computed["heading"], "text": computed["text"],
            "highlights": highlights}


def ask(question: str, deps: Deps, k: int = 10, route: str = "cascade", decompose: bool = True) -> dict:
    from answer import gather_context, generate, validate
    from classify import route_question
    from grounding import computed_source, ungrounded_numbers

    docs = route_question(question, deps.registry, route, deps.llm, deps.router_model)
    chunks, p = gather_context(question, deps.collection, k, deps.embedder, deps.milvus, docs,
                               decompose, deps.registry)
    computed = computed_source(p) if p else None
    sources = chunks + ([computed] if computed else [])
    answer = generate(question, sources, deps.llm, deps.model)
    problems = validate(answer, sources) + ungrounded_numbers(answer, sources)

    by_id = {s["chunk_id"]: s for s in sources}
    # 같은 출처를 여러 문장이 인용하면, 그 문장들의 숫자를 모아 한 번에 찾는다
    cited_text: dict[str, list[str]] = {}
    for s in answer.sentences:
        for cid in s.citations:
            cited_text.setdefault(cid, []).append(s.text)
    views = {}
    for cid, texts in cited_text.items():
        if cid not in by_id:
            continue  # validate()가 이미 문제로 보고한다
        src = by_id[cid]
        views[cid] = computed_view(src, deps) if src.get("computed") else source_view(src, " ".join(texts), deps)

    return {
        "question": question,
        "status": answer.status,
        "missing": answer.missing,
        "sentences": [{"text": s.text, "inferred": s.inferred, "citations": s.citations} for s in answer.sentences],
        "sources": views,
        "route": {"docs": docs, "decomposed": bool(p),
                  "drivers": [d["item"] for d in p["drivers"]] if p else []},
        "problems": problems,
    }


def fact_source(fact: dict, deps: "Deps", locations_by_chunk: dict) -> dict:
    """숫자 테이블의 행 하나 -> /ask의 출처와 같은 모양 (화면이 같은 뷰어로 보여 준다).

    decision 022에서 숫자마다 chunk_id를 저장해 둔 덕분에, 그 청크의 블록 안에서 값을 찾아 행을 칠할 수 있다.
    """
    locations = locations_by_chunk.get(fact["chunk_id"], [])
    highlights = [h for h in locate(deps.pdf(fact["doc_id"]), locations, [pdf_text(fact["value"], fact["unit"])])
                  if h["level"] == "exact"]
    from registry import doc_label, get_doc
    return {"kind": "chunk", "doc_id": fact["doc_id"],
            "label": f"{doc_label(get_doc(fact['doc_id']))} p{fact['page']}",
            "page": fact["page"], "heading": fact["label"], "highlights": highlights}


def report_summary(doc_id: str, deps: "Deps") -> dict:
    from bridge import load_facts
    from registry import doc_label, get_doc

    facts = load_facts(doc_id)
    if not facts:
        raise LookupError(f"no facts for {doc_id}: run backend/numbers/extract.py")
    year = max(f["period"] for f in facts)
    cards, wf = kpis(facts, year), waterfall(facts, year)

    used = {f["chunk_id"] for c in cards for f in c["facts"]} | {b["fact"]["chunk_id"] for b in wf["bars"]}
    rows = deps.milvus.get(deps.collection, ids=sorted(used), output_fields=["locations"])
    locations_by_chunk = {r["chunk_id"]: as_locations(r["locations"]) for r in rows}

    for c in cards:
        c["source"] = fact_source(c["facts"][0], deps, locations_by_chunk)
        if c["computed"]:  # 매출총이익률: 매출총이익과 매출 두 행을 칠한다
            c["source"]["highlights"] += fact_source(c["facts"][1], deps, locations_by_chunk)["highlights"]
        del c["facts"]
    for b in wf["bars"]:
        b["source"] = fact_source(b.pop("fact"), deps, locations_by_chunk)
    return {"doc_id": doc_id, "label": doc_label(get_doc(doc_id)), "year": year, "kpis": cards, "waterfall": wf}