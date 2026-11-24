"""Retrieval evaluation on the golden set: recall@k by page and by quote.

Usage:
    uv run --project backend python evals/eval_retrieval.py --collection docs_v2_2 --body-only
    ... --k 5 --prefix --body-only --note "bge query prefix"
    ... --route rule --k 10 --decompose --coverage 0.8 --note "bridge decomposition"

Metrics (unanswerable questions are skipped):
- page recall@k:  share of gold evidence items whose page (or an alternative's page) is in the top-k
- quote recall@k: share of gold evidence items whose quote (or an alternative's quote) is in the top-k
An evidence item may list `alternatives`: the same fact stated elsewhere in the report.
- gold rank:      where the chunk containing each quote ranks among ALL chunks (diagnosis)

Every run appends one row to evals/results.md so experiments can be compared later.
"""

import argparse
import datetime
import json
import re
from pathlib import Path

import sys

from pymilvus import MilvusClient
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "rag"))
from router import load_docs  # noqa: E402
from classify import route_question  # noqa: E402
from decompose import merge_seats, plan  # noqa: E402
from textnorm import norm  # noqa: E402

DB_PATH = "data/milvus.db"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
BGE_PREFIX = "Represent this sentence for searching relevant passages: "
GOLDEN = Path("evals/golden.jsonl")
FIELDS = ["page", "heading", "locations", "doc_id", "doc_label"]
LLM = {"client": None, "model": None}  # --route cascade|llm 일 때만 채운다
RESULTS = Path("evals/results.md")
DEFAULT_DOC = "amat-10k-fy2025"  # doc이 없는 근거는 첫 문서(FY2025 10-K)의 것이다


def doc_of(evidence: dict) -> str:
    return evidence.get("doc", DEFAULT_DOC)


def hit_id(hit: dict) -> str:
    """검색 결과의 기본 키. pymilvus 버전에 따라 'id' 또는 필드 이름('chunk_id')으로 온다."""
    return hit.get("id", hit.get("chunk_id"))


def chunk_pages(entity: dict) -> set[tuple[str, int]]:
    """청크가 걸쳐 있는 모든 (문서, 페이지). 문서가 여럿이면 페이지 번호만으로는 구분이 안 된다."""
    return {(entity["doc_id"], loc["page"]) for loc in entity["locations"]}


def evaluate(args) -> None:
    golden = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    questions = [q for q in golden if q["type"] != "unanswerable"]

    model = SentenceTransformer(MODEL_NAME)
    client = MilvusClient(DB_PATH)
    if not client.has_collection(args.collection):
        raise SystemExit(f"collection '{args.collection}' not found. available: {client.list_collections()}")
    client.load_collection(args.collection)  # 새로 연 DB는 released 상태라 먼저 불러온다
    total = client.get_collection_stats(args.collection)["row_count"]

    # 전체 청크 텍스트: "인용문이 어느 청크에도 없다"(파싱/정규화 문제)와
    # "있는데 못 찾았다"(검색 문제)를 구분하는 데 쓴다
    all_chunks = client.query(args.collection, filter="", output_fields=["text", "doc_id"], limit=total)
    all_text = {c["chunk_id"]: (c["doc_id"], norm(c["text"])) for c in all_chunks}

    texts = [(BGE_PREFIX if args.prefix else "") + q["question"] for q in questions]
    vectors = model.encode(texts, normalize_embeddings=True)

    registry = load_docs()
    # 출력용 짧은 이름: 10-K는 "K25", 프록시는 "PX26" (프록시도 fiscal_year가 2025라 연도만으로는 구분이 안 된다)
    short = {d["doc_id"]: ("PX" + d["doc_id"][-2:] if d["doc_type"] == "DEF 14A" else f"K{d['fiscal_year'] % 100}")
             for d in registry}
    decomposed_ids = set()
    route_covered, route_extra = 0, 0  # 라우터 정확도: 정답 문서를 다 골랐나, 쓸데없는 문서는 몇 개인가
    page_scores, quote_scores, lines = [], [], []
    for q, vec in zip(questions, vectors):
        docs = question_docs(q, args, registry)
        needed = {doc_of(o) for ev in q["evidence"] for o in [ev, *ev.get("alternatives", [])]}
        if docs:
            # 근거마다 원래 위치나 대안 중 하나의 문서만 골랐으면 된다
            route_covered += all(any(doc_of(o) in docs for o in [ev, *ev.get("alternatives", [])])
                                 for ev in q["evidence"])
            route_extra += len(set(docs) - needed)
        # 순위 진단을 위해 상위 k개가 아니라 전체를 순서대로 받아 온다
        hits = client.search(
            args.collection,
            data=[vec.tolist()],
            limit=total,
            output_fields=FIELDS,
            filter=search_filter(args, docs),
        )[0]
        top = hits[: args.k]
        p = plan(q["question"], registry, args.coverage) if args.decompose else None
        if p:
            # 원인 검색어마다 driver_k개씩 더 찾고, 합쳐서 k개만 남긴다 (k = LLM에 넘길 청크 수)
            driver_vecs = model.encode([d["query"] for d in p["drivers"]], normalize_embeddings=True)
            per_driver = [
                client.search(args.collection, data=[v.tolist()], limit=args.driver_k,
                              output_fields=FIELDS, filter=search_filter(args, docs))[0]
                for v in driver_vecs
            ]
            # 고정석: 브리지 숫자가 나온 청크를 검색 없이 id로 가져온다
            fixed = [] if args.no_statement_seat else [
                {"id": r["chunk_id"], "entity": r}
                for r in client.get(args.collection, ids=p["statement_chunks"], output_fields=FIELDS)
            ]
            top = merge_seats(hits, per_driver, budget=args.k, key=hit_id,
                              fixed=fixed, keep_original=args.keep_original)
            decomposed_ids.add(q["id"])
        top_ids = [hit_id(h) for h in top]

        top_pages = set().union(*(chunk_pages(h["entity"]) for h in top))

        page_hits, quote_hits, details = 0, 0, []
        for ev in q["evidence"]:
            # 같은 사실이 여러 곳에 있을 수 있다: 원래 위치나 alternatives 중 하나면 된다
            options = [ev, *ev.get("alternatives", [])]
            page_hits += any((doc_of(opt), opt["page"]) in top_pages for opt in options)

            # 인용문이 같아도 다른 문서의 청크면 인정하지 않는다
            owners = {cid for opt in options for cid, (d, t) in all_text.items()
                      if d == doc_of(opt) and norm(opt["quote"]) in t}
            if not owners:
                details.append(f"    - p{ev['page']} NOT IN INDEX: {ev['quote']!r}")
                continue
            # 넘긴 k개 안에 있으면 성공. 표시는 넘긴 목록의 자리, 없으면 원래 질문 검색의 순위
            in_top = [(i + 1, h) for i, h in enumerate(top) if hit_id(h) in owners]
            if in_top:
                slot, best = in_top[0]
                quote_hits += 1
                details.append(f"    + {best['entity']['doc_label']} p{best['entity']['page']} slot {slot}: {ev['quote']!r}")
                continue
            ranked = [(i + 1, h) for i, h in enumerate(hits) if hit_id(h) in owners]
            rank, best = ranked[0] if ranked else (None, None)
            where = f"{best['entity']['doc_label']} p{best['entity']['page']}" if best else f"p{ev['page']}"
            details.append(f"    - {where} rank {rank}: {ev['quote']!r}")
        page_recall = page_hits / len(q["evidence"])
        quote_recall = quote_hits / len(q["evidence"])

        page_scores.append(page_recall)
        quote_scores.append(quote_recall)

        retrieved = ", ".join(f"{short[h['entity']['doc_id']]}:p{h['entity']['page']}" for h in top)
        routed = ",".join(short[d] for d in docs) if docs else "all"
        if p:
            routed += " | decompose " + ",".join(d["item"] for d in p["drivers"])
        lines.append(f"{q['id']} [{q['type']}] docs {routed} | page {page_recall:.2f} | quote {quote_recall:.2f} | top{args.k}: {retrieved}")
        lines.extend(details)

    mean_page = sum(page_scores) / len(page_scores)
    mean_quote = sum(quote_scores) / len(quote_scores)

    print("\n".join(lines))
    print(f"\n{args.collection}  k={args.k}  body_only={args.body_only}  docs={args.doc or 'per route'}  route={args.route}"
          f"  decompose={args.decompose}  n={len(questions)}")
    print(f"page recall@{args.k}:  {mean_page:.2f}")
    print(f"quote recall@{args.k}: {mean_quote:.2f}")
    if args.route in ("rule", "cascade", "llm") and not args.doc:
        print(f"router: evidence documents covered {route_covered}/{len(questions)}, "
              f"extra documents searched {route_extra} (sum over questions)")
    if args.decompose:
        # 전체 평균은 분해 대상(몇 개 안 됨)의 변화를 희석한다: 대상만 따로 보여 준다
        sub = [qs for q, qs in zip(questions, quote_scores) if q["id"] in decomposed_ids]
        if sub:
            print(f"  decomposed subset (n={len(sub)}: {', '.join(sorted(decomposed_ids))}) "
                  f"quote recall: {sum(sub) / len(sub):.2f}")

    append_result(args, mean_page, mean_quote, len(questions))


def question_docs(q: dict, args, registry: list[dict]) -> list[str] | None:
    """이 질문을 어느 문서에서 찾을지.

    --doc   : 모든 질문에 같은 문서 (고정 필터)
    --route none   : 필터 없음 (모든 문서)
    --route oracle : 정답 근거가 있는 문서 (라우터가 완벽할 때의 상한선)
    --route rule   : 질문 텍스트만 보고 규칙으로 고름 (router.py)
    --route cascade: 규칙이 확신하지 못할 때만 LLM이 문서 종류를 정함 (classify.py, decision 026)
    """
    if args.doc:
        return args.doc
    if args.route == "oracle":
        return sorted({doc_of(o) for ev in q["evidence"] for o in [ev, *ev.get("alternatives", [])]})
    if args.route in ("rule", "cascade", "llm"):
        return route_question(q["question"], registry, args.route, LLM["client"], LLM["model"])
    return None


def search_filter(args, docs: list[str] | None) -> str:
    parts = []
    if args.body_only:
        parts.append('section == "body"')
    if docs:
        parts.append("doc_id in [" + ", ".join(f'"{d}"' for d in docs) + "]")
    return " and ".join(parts)


def append_result(args, mean_page: float, mean_quote: float, n: int) -> None:
    """실험 기록: 한 번 돌릴 때마다 표에 한 줄."""
    if not RESULTS.exists():
        RESULTS.write_text(
            "# Retrieval results\n\n"
            "| date | collection | k | prefix | body only | page recall | quote recall | n | note |\n"
            "|---|---|---|---|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    row = (
        f"| {datetime.date.today()} | {args.collection} | {args.k} | {args.prefix} | {args.body_only} "
        f"| {mean_page:.2f} | {mean_quote:.2f} | {n} | {args.note}{' [docs: ' + ', '.join(args.doc) + ']' if args.doc else ' [route: ' + args.route + ']'}"
        f"{f' [decompose cov {args.coverage} dk {args.driver_k} keep {args.keep_original}' + (' no-stmt' if args.no_statement_seat else '') + ']' if args.decompose else ''} |\n"
    )
    with open(RESULTS, "a", encoding="utf-8") as f:
        f.write(row)
    print(f"appended to {RESULTS}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Golden-set retrieval evaluation")
    parser.add_argument("--collection", required=True)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--prefix", action="store_true", help="prepend the bge query instruction")
    parser.add_argument("--body-only", action="store_true", help="exclude exhibits")
    parser.add_argument("--doc", action="append", help="search only these doc_ids for every question (repeatable)")
    parser.add_argument("--route", default="none", choices=["none", "oracle", "rule", "cascade", "llm"],
                        help="pick documents per question (ignored when --doc is given)")
    parser.add_argument("--decompose", action="store_true",
                        help="add bridge-driver queries for net-income-change questions; k = chunks passed on")
    parser.add_argument("--coverage", type=float, default=0.8, help="cumulative |contribution| share of drivers")
    parser.add_argument("--driver-k", type=int, default=3, help="hits per driver query")
    parser.add_argument("--keep-original", type=int, default=4, help="seats reserved for the original question")
    parser.add_argument("--no-statement-seat", action="store_true",
                        help="do not add the income statement chunk the bridge used")
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    if args.route in ("cascade", "llm"):
        import os

        from openai import OpenAI
        LLM.update(client=OpenAI(), model=os.environ.get("ROUTER_MODEL") or os.environ.get("OPENAI_MODEL"))
    evaluate(args)