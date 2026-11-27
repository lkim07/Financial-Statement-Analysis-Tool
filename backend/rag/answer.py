"""Question -> retrieve chunks -> LLM answer where every sentence cites its chunks.

Usage:
    uv run --project backend --env-file .env python backend/rag/answer.py "Why did net income decrease?"
    ... --k 5 --collection docs_v3 --doc amat-10k-fy2025
    ... --k 10 --route rule --decompose      # 라우터 + 브리지 분해 (decision 021, 023, 024)

.env needs OPENAI_API_KEY and OPENAI_MODEL.
"""

import argparse
import os
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field
from pymilvus import MilvusClient
from sentence_transformers import SentenceTransformer

from decompose import merge_seats, plan
from grounding import computed_source, ungrounded_numbers
from classify import route_question
from filters import build_filter
from router import load_docs

DB_PATH = "data/milvus.db"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
FIELDS = ["text", "page", "heading", "locations", "doc_id", "doc_label"]


# ---------- 1. 출력 형식: LLM이 반드시 이 모양으로 답하게 한다 ----------

class Sentence(BaseModel):
    text: str = Field(description="One sentence of the answer.")
    citations: list[str] = Field(description="IDs of the context chunks that support this sentence, e.g. ['c0123'].")
    inferred: bool = Field(description="True if the sentence is your own inference or calculation, not stated in the chunks.")


class Answer(BaseModel):
    status: Literal["answered", "partial", "no_evidence"] = Field(
        description="answered: the context fully answers the question. "
        "partial: the context answers part of it (e.g. what changed but not why). "
        "no_evidence: the context contains nothing relevant."
    )
    sentences: list[Sentence]
    missing: str = Field(description="For partial or no_evidence: what information is missing. Empty if answered.")


INSTRUCTIONS = """You answer questions about a company's annual report using ONLY the context chunks provided.

Rules:
- Every sentence must cite at least one chunk ID from the context, in `citations`.
- Copy numbers exactly as they appear in the context. Do not use outside knowledge.
- If a sentence is your own inference or calculation rather than something the report states, set `inferred` to true.
- If the context fully answers the question, set `status` to "answered".
- If it answers only part of the question (for example, it shows WHAT changed but not WHY),
  set `status` to "partial", answer the supported part, and describe what is missing in `missing`.
  Never fill the gap with a plausible guess.
- If the context contains nothing relevant, set `status` to "no_evidence" and return no sentences.
- Never claim the whole report lacks something. Say "the retrieved sections do not state ..." instead,
  because you only see a few excerpts of the report.
- Context chunks may come from different documents (e.g. FY2025 10-K and FY2024 10-K). The same year can
  appear in both, sometimes with different figures (later reports may recast prior years).
  Always say which document a figure comes from when more than one document is involved.
- Some context entries are marked COMPUTED. They are calculations made by code from the report's financial
  statements (for example a net income bridge). Cite them like chunks for the numbers they contain.
  A sentence that only restates a COMPUTED entry is not inferred.
  COMPUTED entries show HOW MUCH each item contributed, not WHY: take the reasons from report chunks.
- When explaining why a total changed, name the largest contributors, including ones that moved
  in the opposite direction (e.g. "despite higher gross profit").
- Be concise: at most 6 sentences."""


# ---------- 2. 검색 ----------

def retrieve(question: str, collection: str, k: int, embedder, milvus, docs: list[str] | None = None,
             company: str | None = None) -> list[dict]:
    vec = embedder.encode([question], normalize_embeddings=True)
    hits = milvus.search(
        collection,
        data=vec.tolist(),
        limit=k,
        output_fields=["text", "page", "heading", "locations", "doc_id", "doc_label"],
        filter=build_filter(docs, company),
    )[0]
    return [{"chunk_id": h.get("id", h.get("chunk_id")), **h["entity"]} for h in hits]


def gather_context(question: str, collection: str, k: int, embedder, milvus, docs: list[str] | None = None,
                   decompose: bool = False, registry: list[dict] | None = None, coverage: float = 0.8,
                   driver_k: int = 3, keep_original: int = 4,
                   company: str | None = None) -> tuple[list[dict], dict | None]:
    """LLM에 넘길 청크 k개 (+ 분해했다면 브리지 계산 결과 하나).

    분해 대상이 아니면 retrieve()와 같다. 분해 대상이면 decision 023의 자리 배정을 따른다.
    """
    p = plan(question, registry or load_docs(), coverage) if decompose else None
    if not p:
        return retrieve(question, collection, k, embedder, milvus, docs, company), None

    vecs = embedder.encode([question] + [d["query"] for d in p["drivers"]], normalize_embeddings=True)
    searches = [milvus.search(collection, data=[v.tolist()], limit=k if i == 0 else driver_k,
                              output_fields=FIELDS, filter=build_filter(docs, company))[0]
                for i, v in enumerate(vecs)]
    fixed = [{"id": r["chunk_id"], "entity": r}
             for r in milvus.get(collection, ids=p["statement_chunks"], output_fields=FIELDS)]
    key = lambda h: h.get("id", h.get("chunk_id"))  # noqa: E731
    merged = merge_seats(searches[0], searches[1:], budget=k, key=key, fixed=fixed, keep_original=keep_original)
    chunks = [{"chunk_id": key(h), **h["entity"]} for h in merged]
    return chunks, p


def missing_docs(milvus, collection: str, registry: list[dict]) -> list[str]:
    """레지스트리에는 있는데 컬렉션에는 청크가 하나도 없는 문서.

    라우터가 그런 문서를 고르면 검색 결과가 0개가 되고, LLM은 "근거 없음"이라고 정직하게 답한다.
    오류가 아니라 그럴듯한 답으로 보이기 때문에 미리 막는다 (Phase 7: docs_v2_2에는 프록시가 없었다).
    """
    missing = []
    for d in registry:
        rows = milvus.query(collection, filter=f'doc_id == "{d["doc_id"]}"', output_fields=["doc_id"], limit=1)
        if not rows:
            missing.append(d["doc_id"])
    return missing


def build_context(chunks: list[dict]) -> str:
    """청크마다 ID와 출처를 머리에 붙인다. LLM은 이 ID로 인용한다."""
    parts = [f"[{c['chunk_id']}] ({c['doc_label']}, page {c['page']}, {c['heading']})\n{c['text']}" for c in chunks]
    return "\n\n---\n\n".join(parts)


# ---------- 3. 생성 ----------

def generate(question: str, chunks: list[dict], llm: OpenAI, model: str) -> Answer:
    response = llm.responses.parse(
        model=model,
        instructions=INSTRUCTIONS,
        input=f"Context:\n\n{build_context(chunks)}\n\nQuestion: {question}",
        text_format=Answer,  # Pydantic 모델을 넘기면 SDK가 JSON 스키마로 바꿔 형식을 강제한다
    )
    return response.output_parsed


# ---------- 4. 검증: LLM을 믿지 않고 코드로 확인한다 ----------

def validate(answer: Answer, chunks: list[dict]) -> list[str]:
    """인용이 실제로 받은 청크를 가리키는지 확인한다. 문제 목록을 돌려준다."""
    known = {c["chunk_id"] for c in chunks}
    problems = []
    for i, s in enumerate(answer.sentences, start=1):
        if not s.citations:
            problems.append(f"sentence {i}: no citation")
        for cid in s.citations:
            if cid not in known:
                problems.append(f"sentence {i}: cites unknown chunk {cid!r}")
    if answer.status == "no_evidence" and answer.sentences:
        problems.append("status=no_evidence but sentences were returned")
    if answer.status == "partial" and not answer.missing:
        problems.append("status=partial but nothing listed as missing")
    return problems


def show(answer: Answer, chunks: list[dict], problems: list[str]) -> None:
    where = {c["chunk_id"]: f"{c['doc_label']} p{c['page']}" for c in chunks}
    print(f"[{answer.status}]")
    for s in answer.sentences:
        refs = ", ".join(where.get(cid, f"{cid}?") for cid in s.citations)
        tag = " (inferred)" if s.inferred else ""
        print(f"- {s.text}{tag}  [{refs}]")
    if answer.missing:
        print(f"missing: {answer.missing}")
    print("\nretrieved:", ", ".join(where[c["chunk_id"]] for c in chunks))
    print("citation check:", "OK" if not problems else problems)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--collection", default="docs_v3")
    parser.add_argument("--doc", action="append", help="limit to these doc_ids (repeatable)")
    parser.add_argument("--route", choices=["none", "rule", "cascade", "llm"], default="none",
                        help="pick documents from the question (cascade: rules, LLM only when unsure)")
    parser.add_argument("--decompose", action="store_true", help="bridge decomposition for net income questions")
    args = parser.parse_args()

    embedder = SentenceTransformer(EMBED_MODEL)
    milvus = MilvusClient(DB_PATH)
    milvus.load_collection(args.collection)
    llm = OpenAI()  # OPENAI_API_KEY 환경 변수를 읽는다

    registry = load_docs()
    if missing := missing_docs(milvus, args.collection, registry):
        raise SystemExit(f"collection '{args.collection}' has no chunks for: {', '.join(missing)}")
    router_model = os.environ.get("ROUTER_MODEL") or os.environ["OPENAI_MODEL"]
    docs = args.doc or route_question(args.question, registry, args.route, llm, router_model)
    chunks, p = gather_context(args.question, args.collection, args.k, embedder, milvus, docs,
                               args.decompose, registry)
    if p:
        chunks.append(computed_source(p))
        print("decompose:", ", ".join(f"{d['item']} {d['contribution']:+,.0f}" for d in p["drivers"]))
    answer = generate(args.question, chunks, llm, os.environ["OPENAI_MODEL"])
    show(answer, chunks, validate(answer, chunks) + ungrounded_numbers(answer, chunks))


if __name__ == "__main__":
    main()
