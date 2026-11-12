"""Retrieval evaluation on the golden set: recall@k by page and by quote.

Usage:
    uv run --project backend python evals/eval_retrieval.py --collection chunks_llamaparse_v2_2 --k 5 --prefix --body-only --note "bge query prefix"

Metrics (unanswerable questions are skipped):
- page recall@k:  share of gold evidence pages that appear in the top-k chunks' locations
- quote recall@k: share of gold evidence quotes contained in the top-k chunks' text
- gold rank:      where the chunk containing each quote ranks among ALL chunks (diagnosis)

Every run appends one row to evals/results.md so experiments can be compared later.
"""

import argparse
import datetime
import json
import re
from pathlib import Path

from pymilvus import MilvusClient
from sentence_transformers import SentenceTransformer

DB_PATH = "data/milvus.db"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
BGE_PREFIX = "Represent this sentence for searching relevant passages: "
GOLDEN = Path("evals/golden.jsonl")
RESULTS = Path("evals/results.md")


def norm(text: str) -> str:
    """두 파서의 표기 차이를 지운다: 마크다운/HTML 기호, 표의 |, 특수 공백, '7 %' vs '7%'."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*#|]", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+%", "%", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def hit_id(hit: dict) -> str:
    """검색 결과의 기본 키. pymilvus 버전에 따라 'id' 또는 필드 이름('chunk_id')으로 온다."""
    return hit.get("id", hit.get("chunk_id"))


def chunk_pages(entity: dict) -> set[int]:
    """청크가 걸쳐 있는 모든 페이지 (페이지를 넘어 합친 청크도 있다)."""
    return {loc["page"] for loc in entity["locations"]}


def evaluate(args) -> None:
    golden = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]
    questions = [q for q in golden if q["type"] != "unanswerable"]

    model = SentenceTransformer(MODEL_NAME)
    client = MilvusClient(DB_PATH)
    
    if not client.has_collection(args.collection):
        raise SystemExit(f"collection '{args.collection}' not found. available: {client.list_collections()}")

    client.load_collection(args.collection)  # 새로 연 DB는 released 상태라 먼저 불러온다
    total = client.get_collection_stats(args.collection)["row_count"]

    # 전체 청크 텍스트: "인용문이 어느 청크에도 없다"(파싱/정규화 문제)와
    # "있는데 못 찾았다"(검색 문제)를 구분하는 데 쓴다
    all_chunks = client.query(args.collection, filter="", output_fields=["text"], limit=total)
    all_text = {c["chunk_id"]: norm(c["text"]) for c in all_chunks}

    texts = [(BGE_PREFIX if args.prefix else "") + q["question"] for q in questions]
    vectors = model.encode(texts, normalize_embeddings=True)

    page_scores, quote_scores, lines = [], [], []
    for q, vec in zip(questions, vectors):
        # 순위 진단을 위해 상위 k개가 아니라 전체를 순서대로 받아 온다
        hits = client.search(
            args.collection,
            data=[vec.tolist()],
            limit=total,
            output_fields=["page", "heading", "locations"],
            filter='section == "body"' if args.body_only else "",
        )[0]
        top = hits[: args.k]

        gold_pages = {ev["page"] for ev in q["evidence"]}
        top_pages = set().union(*(chunk_pages(h["entity"]) for h in top))
        page_recall = len(gold_pages & top_pages) / len(gold_pages)

        quote_hits, details = 0, []
        for ev in q["evidence"]:
            quote = norm(ev["quote"])
            owners = [cid for cid, t in all_text.items() if quote in t]
            if not owners:
                details.append(f"    - p{ev['page']} NOT IN INDEX: {ev['quote']!r}")
                continue
            rank = next((i + 1 for i, h in enumerate(hits) if hit_id(h) in owners), None)
            if rank is not None and rank <= args.k:
                quote_hits += 1
                details.append(f"    + p{ev['page']} rank {rank}: {ev['quote']!r}")
            else:
                details.append(f"    - p{ev['page']} rank {rank}: {ev['quote']!r}")
        quote_recall = quote_hits / len(q["evidence"])

        page_scores.append(page_recall)
        quote_scores.append(quote_recall)

        retrieved = ", ".join(f"p{h['entity']['page']}" for h in top)
        lines.append(f"{q['id']} [{q['type']}] page {page_recall:.2f} | quote {quote_recall:.2f} | top{args.k}: {retrieved}")
        lines.extend(details)

    mean_page = sum(page_scores) / len(page_scores)
    mean_quote = sum(quote_scores) / len(quote_scores)

    print("\n".join(lines))
    print(f"\n{args.collection}  k={args.k}  prefix={args.prefix}  body_only={args.body_only}")
    print(f"page recall@{args.k}:  {mean_page:.2f}")
    print(f"quote recall@{args.k}: {mean_quote:.2f}")

    append_result(args, mean_page, mean_quote, len(questions))


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
        f"| {mean_page:.2f} | {mean_quote:.2f} | {n} | {args.note} |\n"
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
    parser.add_argument("--note", default="")
    evaluate(parser.parse_args())