"""Answer evaluation on the golden set: status, citation validity, evidence hit.

Usage:
    uv run --project backend --env-file .env python evals/eval_answer.py --note "baseline prompt"

Metrics:
- status ok:      unanswerable -> no_evidence; every other question -> answered or partial
- citation valid: every sentence cites at least one chunk, and only chunks it was given
- evidence hit:   share of golden quotes found inside the chunks the answer actually cited
                  (retrieval caps this: a quote that was never retrieved cannot be cited)

Each run saves every answer to evals/runs/ (for later review and LLM-judge scoring in Phase 5)
and appends one summary row to evals/results_answer.md.
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from openai import OpenAI
from pymilvus import MilvusClient
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "rag"))
from answer import generate, retrieve, validate  # noqa: E402
from eval_retrieval import DB_PATH, GOLDEN, norm
from eval_retrieval import MODEL_NAME as EMBED_MODEL_NAME  # noqa: E402

RUNS_DIR = Path("evals/runs")
RESULTS = Path("evals/results_answer.md")


def main(args) -> None:
    golden = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    model_name = os.environ["OPENAI_MODEL"]

    embedder = SentenceTransformer(EMBED_MODEL_NAME)
    milvus = MilvusClient(DB_PATH)
    milvus.load_collection(args.collection)
    llm = OpenAI()

    records, status_ok, valid, hit_scores = [], 0, 0, []
    for q in golden:
        chunks = retrieve(q["question"], args.collection, args.k, embedder, milvus)
        answer = generate(q["question"], chunks, llm, model_name)
        problems = validate(answer, chunks)

        # 1) status: 답이 없는 질문은 no_evidence, 나머지는 answered 또는 partial이어야 한다
        expected_none = q["type"] == "unanswerable"
        ok = (answer.status == "no_evidence") == expected_none
        status_ok += ok

        # 2) 인용 형식
        valid += not problems

        # 3) 실제로 인용한 청크 안에 golden 인용문이 들어 있나
        cited = {cid for s in answer.sentences for cid in s.citations}
        cited_text = " ".join(norm(c["text"]) for c in chunks if c["chunk_id"] in cited)
        if q["evidence"]:
            hits = sum(norm(ev["quote"]) in cited_text for ev in q["evidence"])
            hit_scores.append(hits / len(q["evidence"]))
            hit_str = f"{hits}/{len(q['evidence'])}"
        else:
            hit_str = "-"

        print(f"{q['id']} [{q['type']:<12}] status {answer.status:<11} {'ok ' if ok else 'BAD'} "
              f"| citations {'ok' if not problems else 'BAD'} | evidence {hit_str}")
        if answer.missing:
            print(f"    missing: {answer.missing}")
        for p in problems:
            print(f"    ! {p}")

        records.append({
            "id": q["id"],
            "question": q["question"],
            "status": answer.status,
            "sentences": [s.model_dump() for s in answer.sentences],
            "missing": answer.missing,
            "retrieved": [c["chunk_id"] for c in chunks],
            "problems": problems,
        })

    n = len(golden)
    mean_hit = sum(hit_scores) / len(hit_scores)
    print(f"\nstatus ok {status_ok}/{n} | citations valid {valid}/{n} | evidence hit {mean_hit:.2f}")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_path = RUNS_DIR / f"answers_{stamp}.jsonl"
    with open(run_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"saved answers -> {run_path}")

    if not RESULTS.exists():
        RESULTS.write_text(
            "# Answer results\n\n"
            "| date | model | collection | k | status ok | citations valid | evidence hit | run | note |\n"
            "|---|---|---|---|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    with open(RESULTS, "a", encoding="utf-8") as f:
        f.write(f"| {datetime.date.today()} | {model_name} | {args.collection} | {args.k} "
                f"| {status_ok}/{n} | {valid}/{n} | {mean_hit:.2f} | {run_path.name} | {args.note} |\n")
    print(f"appended to {RESULTS}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Golden-set answer evaluation")
    parser.add_argument("--collection", default="chunks_llamaparse_v2_2")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--note", default="")
    main(parser.parse_args())