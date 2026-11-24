"""라우터 비교: 규칙 / LLM / 계단식 (decision 026).

정답 라벨은 golden에서 만든다:
- 문서 종류: 근거(evidence)의 원래 위치가 있는 문서들의 종류. 답 없는 질문은 area가 Proxy면 DEF 14A, 아니면 10-K
- 질문 유형: type 필드 (답 없는 질문은 채점하지 않는다)

LLM은 질문마다 한 번만 부르고 결과를 재사용한다: 계단식의 비용 = 규칙이 확신하지 못한 질문의 LLM 비용.

Usage:
    uv run --project backend --env-file .env python evals/eval_router.py --note "first comparison"
    ROUTER_MODEL=<더 작은 모델> ... 로 분류기 모델만 바꿀 수 있다 (없으면 OPENAI_MODEL)
"""

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "rag"))
from classify import llm_classify, rule_classify  # noqa: E402
from router import PROXY, TEN_K, load_docs  # noqa: E402

GOLDEN = Path("evals/golden.jsonl")
RESULTS = Path("evals/results_router.md")
DEFAULT_DOC = "amat-10k-fy2025"
ORDER = {TEN_K: 0, PROXY: 1}


def gold_doc_types(q: dict, type_of: dict) -> list[str]:
    if not q["evidence"]:
        return [PROXY] if q.get("area") == "Proxy" else [TEN_K]
    types = {type_of[ev.get("doc", DEFAULT_DOC)] for ev in q["evidence"]}
    return sorted(types, key=ORDER.get)


def score(rows: list[dict], key: str) -> dict:
    """rows: 질문마다 {"gold_docs", "gold_type", key: 분류}"""
    exact = sum(r[key]["doc_types"] == r["gold_docs"] for r in rows)
    covers = sum(set(r["gold_docs"]) <= set(r[key]["doc_types"]) for r in rows)
    typed = [r for r in rows if r["gold_type"] != "unanswerable"]
    type_ok = sum(r[key]["question_type"] == r["gold_type"] for r in typed)
    return {"n": len(rows), "doc_exact": exact, "doc_covers": covers, "n_typed": len(typed), "type_ok": type_ok}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--note", default="")
    parser.add_argument("--no-llm", action="store_true", help="rule only (no API calls)")
    args = parser.parse_args()

    golden = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    type_of = {d["doc_id"]: d["doc_type"] for d in load_docs()}

    client, model = None, os.environ.get("ROUTER_MODEL") or os.environ.get("OPENAI_MODEL")
    if not args.no_llm:
        from openai import OpenAI
        client = OpenAI()

    rows = []
    for q in golden:
        rule = rule_classify(q["question"])
        row = {"id": q["id"], "question": q["question"], "gold_docs": gold_doc_types(q, type_of),
               "gold_type": q["type"], "rule": rule}
        if client:
            start = time.perf_counter()
            llm, tokens = llm_classify(q["question"], client, model)
            row.update(llm=llm, tokens=tokens, seconds=time.perf_counter() - start)
            row["cascade"] = rule if rule["confident"] else llm
        rows.append(row)

    modes = ["rule"] + (["llm", "cascade"] if client else [])
    print(f"\nn={len(rows)} (question type scored on {sum(r['gold_type'] != 'unanswerable' for r in rows)})"
          f"  router model: {model if client else '-'}\n")
    print(f"{'mode':<8} {'doc exact':>10} {'doc covers':>11} {'type':>8} {'LLM calls':>10} {'tokens':>8} {'sec':>6}")
    summary = {}
    for m in modes:
        s = score(rows, m)
        if m == "rule":
            calls = []
        elif m == "llm":
            calls = rows
        else:
            calls = [r for r in rows if not r["rule"]["confident"]]
        tokens = sum(r["tokens"]["input_tokens"] + r["tokens"]["output_tokens"] for r in calls)
        seconds = sum(r["seconds"] for r in calls)
        summary[m] = {**s, "calls": len(calls), "tokens": tokens, "seconds": seconds}
        print(f"{m:<8} {s['doc_exact']:>5}/{s['n']:<4} {s['doc_covers']:>6}/{s['n']:<4} "
              f"{s['type_ok']:>3}/{s['n_typed']:<4} {len(calls):>10} {tokens:>8} {seconds:>6.1f}")

    print("\ndisagreements with the golden labels:")
    for r in rows:
        for m in modes:
            got = r[m]
            bad_doc = got["doc_types"] != r["gold_docs"]
            bad_type = r["gold_type"] != "unanswerable" and got["question_type"] != r["gold_type"]
            if bad_doc or bad_type:
                what = []
                if bad_doc:
                    what.append(f"docs {got['doc_types']} (gold {r['gold_docs']})")
                if bad_type:
                    what.append(f"type {got['question_type']} (gold {r['gold_type']})")
                print(f"  {r['id']} {m:<8} {'; '.join(what)}")
                if m != "rule":
                    print(f"      reason: {got['reason']}")
    unsure = [r["id"] for r in rows if not r["rule"]["confident"]]
    print(f"\nrule unsure ({len(unsure)}): {', '.join(unsure)}")

    new = not RESULTS.exists()
    with RESULTS.open("a", encoding="utf-8") as f:
        if new:
            f.write("# Router results\n\n| date | model | mode | doc exact | doc covers | type | LLM calls | tokens | note |\n"
                    "|---|---|---|---|---|---|---|---|---|\n")
        date = datetime.date.today().isoformat()
        for m, s in summary.items():
            f.write(f"| {date} | {model if m != 'rule' else '-'} | {m} | {s['doc_exact']}/{s['n']} | "
                    f"{s['doc_covers']}/{s['n']} | {s['type_ok']}/{s['n_typed']} | {s['calls']} | {s['tokens']} "
                    f"| {args.note} |\n")
    print(f"appended to {RESULTS}")


if __name__ == "__main__":
    main()