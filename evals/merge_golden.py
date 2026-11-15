"""Merge candidate questions into the golden set.

Usage: uv run --project backend python evals/merge_golden.py evals/golden_candidates.jsonl

- Questions with an id that already exists are replaced by the candidate version.
- Duplicate ids inside golden.jsonl are collapsed (the last occurrence wins).
- Output is sorted by question number, one JSON object per line, ending with a newline.
"""

import json
import re
import sys
from pathlib import Path

GOLDEN = Path("evals/golden.jsonl")


def read_rows(path: Path) -> list[dict]:
    """jsonl을 읽는다. 한 줄에 객체가 여러 개 붙어 있어도(줄바꿈 누락) 하나씩 꺼낸다."""
    decoder = json.JSONDecoder()
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        while line:
            obj, end = decoder.raw_decode(line)  # 앞에서부터 JSON 하나만 읽고, 끝난 위치를 알려 준다
            rows.append(obj)
            line = line[end:].strip()
    return rows


def qnum(row: dict) -> int:
    """'q07' -> 7. 정렬용."""
    return int(re.sub(r"\D", "", row["id"]) or 0)


def main(candidates_path: str) -> None:
    merged = {}
    for row in read_rows(GOLDEN) + read_rows(Path(candidates_path)):
        merged[row["id"]] = row  # 같은 id면 나중 것이 이긴다

    rows = sorted(merged.values(), key=qnum)
    with open(GOLDEN, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{GOLDEN}: {len(rows)} questions ({rows[0]['id']} .. {rows[-1]['id']})")


if __name__ == "__main__":
    main(sys.argv[1])