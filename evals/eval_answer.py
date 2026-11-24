"""Answer evaluation on the golden set: status, citation validity, evidence hit.

Usage:
    uv run --project backend --env-file .env python evals/eval_answer.py --note "baseline prompt"
    ... --k 10 --route rule --decompose --note "bridge"      # decision 024
    ... --only q01 q42                                         # 몇 문항만 (빠르고 싸게 확인)

Metrics:
- status ok:      unanswerable -> no_evidence; every other question -> answered or partial
- citation valid: every sentence cites at least one chunk, and only chunks it was given
- evidence hit:   share of golden quotes found inside the chunks the answer actually cited
                  (retrieval caps this: a quote that was never retrieved cannot be cited)
- numbers ok:     every number in a non-inferred sentence appears in the sources it cites (code check)

Each run saves every answer to evals/runs/ (for later review and LLM-judge scoring in Phase 5)
and appends one summary row to evals/results_answer.md.
"""

import argparse
import collections
import datetime
import json
import os
import sys
from pathlib import Path

from openai import OpenAI
from pymilvus import MilvusClient
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "rag"))
from answer import gather_context, generate, validate  # noqa: E402
from grounding import computed_source, expand_citations, ungrounded_numbers  # noqa: E402
from router import load_docs  # noqa: E402
from classify import route_question  # noqa: E402
from eval_retrieval import DB_PATH, GOLDEN, doc_of, norm
from eval_retrieval import MODEL_NAME as EMBED_MODEL_NAME  # noqa: E402

RUNS_DIR = Path("evals/runs")
RESULTS = Path("evals/results_answer.md")


def main(args) -> None:
    golden = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    model_name = os.environ["OPENAI_MODEL"]
    router_model = os.environ.get("ROUTER_MODEL") or model_name

    embedder = SentenceTransformer(EMBED_MODEL_NAME)
    milvus = MilvusClient(DB_PATH)
    milvus.load_collection(args.collection)
    llm = OpenAI()

    if args.only:
        golden = [q for q in golden if q["id"] in set(args.only)]
    registry = load_docs()

    records, status_ok, valid, numbers_ok, hit_scores = [], 0, 0, 0, []
    for q in golden:
        docs = args.doc or route_question(q["question"], registry, args.route, llm, router_model)
        chunks, p = gather_context(q["question"], args.collection, args.k, embedder, milvus, docs,
                                   args.decompose, registry)
        computed = computed_source(p) if p else None
        sources = chunks + ([computed] if computed else [])
        answer = generate(q["question"], sources, llm, model_name)
        problems = validate(answer, sources)
        number_problems = ungrounded_numbers(answer, sources)
        numbers_ok += not number_problems

        # 1) status: 답이 없는 질문은 no_evidence, 나머지는 answered 또는 partial이어야 한다
        expected_none = q["type"] == "unanswerable"
        if expected_none:
            ok = answer.status != "answered"  # 없다고 하거나, 관련 사실만 주고 없는 부분을 밝히면 통과 (decision 015)
        else:
            ok = answer.status != "no_evidence"
        status_ok += ok

        # 2) 인용 형식
        valid += not problems

        # 3) 실제로 인용한 청크 안에 golden 인용문이 들어 있나
        cited = {cid for s in answer.sentences for cid in s.citations}
        # 브리지를 인용했으면 브리지가 쓴 손익계산서 청크를 인용한 것으로 본다 (그 행이 하이라이트된다)
        cited = expand_citations(cited, sources)
        # 문서별로 인용한 텍스트를 모은다: 근거는 같은 문서의 청크에서 찾아야 인정한다
        cited_text = collections.defaultdict(str)
        for c in chunks:
            if c["chunk_id"] in cited:  # 계산 출처(브리지)는 chunks에 없어서 여기서 자연히 빠진다
                cited_text[c["doc_id"]] += " " + norm(c["text"])
        if q["evidence"]:
            # 원래 인용문이나 alternatives 중 하나라도 인용한 청크 안에 있으면 성공
            hits = sum(
                any(norm(opt["quote"]) in cited_text[doc_of(opt)] for opt in [ev, *ev.get("alternatives", [])])
                for ev in q["evidence"]
            )
            hit_scores.append(hits / len(q["evidence"]))
            hit_str = f"{hits}/{len(q['evidence'])}"
        else:
            hit_str = "-"

        print(f"{q['id']} [{q['type']:<12}] status {answer.status:<11} {'ok ' if ok else 'BAD'} "
              f"| citations {'ok' if not problems else 'BAD'} | numbers {'ok' if not number_problems else 'BAD'} "
              f"| evidence {hit_str}{' | bridge' if p else ''}")
        if answer.missing:
            print(f"    missing: {answer.missing}")
        for msg in problems + number_problems:
            print(f"    ! {msg}")

        records.append({
            "id": q["id"],
            "question": q["question"],
            "status": answer.status,
            "sentences": [s.model_dump() for s in answer.sentences],
            "missing": answer.missing,
            "retrieved": [c["chunk_id"] for c in chunks],
            "problems": problems,
            "number_problems": number_problems,
            # 판정관이 계산 출처를 읽을 수 있게 텍스트를 같이 저장한다 (청크 파일에는 없다)
            "computed": {computed["chunk_id"]: computed["text"]} if computed else {},
        })

    n = len(golden)
    mean_hit = sum(hit_scores) / len(hit_scores)
    print(f"\nstatus ok {status_ok}/{n} | citations valid {valid}/{n} | numbers ok {numbers_ok}/{n} "
          f"| evidence hit {mean_hit:.2f}")

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
                f"| {status_ok}/{n} | {valid}/{n} | {mean_hit:.2f} | {run_path.name} "
                f"| {args.note} [route {args.route}{', decompose' if args.decompose else ''}, numbers ok {numbers_ok}/{n}] |\n")
    print(f"appended to {RESULTS}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Golden-set answer evaluation")
    parser.add_argument("--collection", default="docs_v2_2")
    parser.add_argument("--doc", action="append", help="search only these doc_ids (repeatable)")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--route", choices=["none", "rule", "cascade", "llm"], default="none")
    parser.add_argument("--decompose", action="store_true")
    parser.add_argument("--only", nargs="+", help="question ids to run (e.g. --only q01 q42)")
    parser.add_argument("--note", default="")
    main(parser.parse_args())
