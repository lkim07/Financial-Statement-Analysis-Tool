"""Faithfulness check: does each answer sentence follow from the chunks it cites?

An LLM judge reads one sentence and only the chunks that sentence cites, and labels it:
  supported            every claim (numbers, causes) is stated in the cited chunks,
                       or, for an `inferred` sentence, follows correctly from them
  partially_supported  some claims are supported, others are not
  unsupported          the main claim is not in the cited chunks, or contradicts them

Usage (no answers are regenerated; it reads a saved run):
  1) judge:  uv run --project backend --env-file .env python evals/judge_faithfulness.py judge evals/runs/answers_XXXX.jsonl
  2) sample: uv run --project backend python evals/judge_faithfulness.py sample evals/runs/judge_answers_XXXX.jsonl --n 20
             -> evals/judge_labels.csv (judge labels hidden). Fill `human_label` yourself.
  3) agree:  uv run --project backend python evals/judge_faithfulness.py agree evals/runs/judge_answers_XXXX.jsonl evals/judge_labels.csv

  4) adversarial: uv run --project backend --env-file .env python evals/judge_faithfulness.py adversarial evals/judge_adversarial.jsonl
             planted errors with known labels: does the judge catch them?

Steps 2-4 check the judge itself before we trust its scores.
"""

import argparse
import collections
import csv
import json
import os
import random
from pathlib import Path
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field

CHUNKS = Path("data/chunks/llamaparse.jsonl")
LABELS = ["supported", "partially_supported", "unsupported"]


class Verdict(BaseModel):
    label: Literal["supported", "partially_supported", "unsupported"]
    reason: str = Field(description="One short sentence explaining the label, naming any unsupported claim.")


JUDGE_INSTRUCTIONS = """You check whether a sentence from an answer about an annual report is supported by its cited sources.
Use ONLY the sources given. Do not use outside knowledge.

Labels:
- supported: every factual claim in the sentence (numbers, dates, causes, comparisons) is stated in the sources.
  If the sentence is marked INFERRED, it is supported when its inputs are in the sources and the inference
  or calculation is correct.
- partially_supported: some claims are supported, but at least one is not in the sources.
- unsupported: the main claim is not in the sources, or contradicts them.

Statements that something is NOT in the report (e.g. "the report does not state X") are supported
if the sources indeed do not contain X."""


def load_chunks() -> dict[str, str]:
    with open(CHUNKS, encoding="utf-8") as f:
        return {c["chunk_id"]: c["text"] for c in (json.loads(line) for line in f if line.strip())}


def judge_sentence(llm, model: str, chunks: dict[str, str], question: str, sentence: str,
                   citations: list[str], inferred: bool) -> Verdict:
    """문장 하나를, 그 문장이 인용한 청크만 보여 주고 판정한다."""
    sources = "\n\n---\n\n".join(f"[{cid}]\n{chunks.get(cid, '(missing chunk)')}" for cid in citations)
    tag = "INFERRED " if inferred else ""
    response = llm.responses.parse(
        model=model,
        instructions=JUDGE_INSTRUCTIONS,
        input=f"Sources:\n\n{sources}\n\nQuestion: {question}\n\n{tag}Sentence: {sentence}",
        text_format=Verdict,
    )
    return response.output_parsed


def judge(run_path: Path) -> None:
    chunks = load_chunks()
    llm = OpenAI()
    model = os.environ.get("JUDGE_MODEL") or os.environ["OPENAI_MODEL"]
    out_path = run_path.parent / f"judge_{run_path.name}"

    results = []
    for line in run_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        answer = json.loads(line)
        for i, s in enumerate(answer["sentences"], start=1):
            v = judge_sentence(llm, model, chunks, answer["question"], s["text"], s["citations"], s["inferred"])
            results.append({"key": f"{answer['id']}-s{i}", "id": answer["id"], "sentence": s["text"],
                            "citations": s["citations"], "inferred": s["inferred"],
                            "label": v.label, "reason": v.reason})
            mark = {"supported": " ", "partially_supported": "~", "unsupported": "!"}[v.label]
            print(f"{mark} {answer['id']}-s{i:<2} {v.label:<20} {v.reason}")

    counts = collections.Counter(r["label"] for r in results)
    n = len(results)
    print(f"\nsentences: {n} | " + " | ".join(f"{k} {counts[k]} ({counts[k] / n:.0%})" for k in LABELS))
    print(f"judge model: {model}")

    with open(out_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"saved -> {out_path}")


def sample(judge_path: Path, n: int, seed: int) -> None:
    """사람이 직접 채점할 문장을 무작위로 고른다. 판정관의 답은 숨긴다(먼저 보면 따라가게 된다)."""
    rows = [json.loads(line) for line in judge_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    chunks = load_chunks()
    picked = random.Random(seed).sample(rows, min(n, len(rows)))
    out = Path("evals/judge_labels.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: 엑셀에서 열어도 안 깨진다
        w = csv.writer(f)
        w.writerow(["key", "inferred", "sentence", "sources", "human_label"])
        for r in picked:
            sources = " || ".join(f"[{cid}] {chunks.get(cid, '')}" for cid in r["citations"])
            w.writerow([r["key"], r["inferred"], r["sentence"], sources, ""])
    print(f"{len(picked)} sentences -> {out}. Fill human_label with one of: {', '.join(LABELS)}")


def agree(judge_path: Path, labels_path: Path) -> None:
    """사람 라벨과 판정관 라벨이 얼마나 일치하는지."""
    judged = {r["key"]: r for r in (json.loads(l) for l in judge_path.read_text(encoding="utf-8").splitlines() if l.strip())}
    with open(labels_path, encoding="utf-8-sig") as f:
        human = [row for row in csv.DictReader(f) if row["human_label"].strip()]

    pairs = [(row["human_label"].strip(), judged[row["key"]]["label"]) for row in human]
    same = sum(h == j for h, j in pairs)
    print(f"agreement: {same}/{len(pairs)} ({same / len(pairs):.0%})\n")

    # 혼동 행렬: 행 = 사람, 열 = 판정관
    print(f"{'human \\ judge':<22}" + "".join(f"{l[:12]:>14}" for l in LABELS))
    for h in LABELS:
        print(f"{h:<22}" + "".join(f"{sum(1 for a, b in pairs if a == h and b == j):>14}" for j in LABELS))

    print("\ndisagreements:")
    for row in human:
        j = judged[row["key"]]
        if row["human_label"].strip() != j["label"]:
            print(f"- {row['key']}: you={row['human_label'].strip()}, judge={j['label']} | {j['reason']}")


def adversarial(cases_path: Path) -> None:
    """일부러 틀린 문장을 심어서, 판정관이 잡아내는지 본다. 정답 라벨이 확실한 시험이다."""
    cases = [json.loads(l) for l in cases_path.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    chunks = load_chunks()
    llm = OpenAI()
    model = os.environ.get("JUDGE_MODEL") or os.environ["OPENAI_MODEL"]

    caught, exact, planted, controls_ok, controls = 0, 0, 0, 0, 0
    by_kind = collections.defaultdict(lambda: [0, 0])  # kind -> [caught, total]
    for c in cases:
        v = judge_sentence(llm, model, chunks, c["question"], c["sentence"], c["citations"], c["inferred"])
        exact += v.label == c["expected"]
        if c["kind"] == "control":
            controls += 1
            ok = v.label == "supported"
            controls_ok += ok
        else:
            planted += 1
            ok = v.label != "supported"  # 틀린 문장을 supported라고 하지만 않으면 "잡았다"
            caught += ok
            by_kind[c["kind"]][0] += ok
            by_kind[c["kind"]][1] += 1
        print(f"{'ok ' if ok else 'MISS'} {c['key']:<14} {c['kind']:<10} expected {c['expected']:<20} judge {v.label:<20} {v.reason}")

    print(f"\nplanted errors caught: {caught}/{planted} | controls kept supported: {controls_ok}/{controls} "
          f"| exact label match: {exact}/{len(cases)}")
    print("by kind: " + ", ".join(f"{k} {a}/{b}" for k, (a, b) in sorted(by_kind.items())))
    print(f"judge model: {model}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Faithfulness judge")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("judge"); p.add_argument("run", type=Path)
    p = sub.add_parser("sample"); p.add_argument("judged", type=Path); p.add_argument("--n", type=int, default=20); p.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("agree"); p.add_argument("judged", type=Path); p.add_argument("labels", type=Path)
    p = sub.add_parser("adversarial"); p.add_argument("cases", type=Path)
    args = parser.parse_args()

    if args.command == "judge":
        judge(args.run)
    elif args.command == "sample":
        sample(args.judged, args.n, args.seed)
    elif args.command == "agree":
        agree(args.judged, args.labels)
    else:
        adversarial(args.cases)