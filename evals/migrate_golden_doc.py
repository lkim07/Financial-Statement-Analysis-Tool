"""One-off migration: add "doc": "amat-10k-fy2025" to every evidence item and alternative.

Usage: uv run --project backend python evals/migrate_golden_doc.py

Before Phase 6 every golden question was about the FY2025 10-K, so page numbers were enough.
With several documents, every evidence location must name its document.
Safe to run twice: existing "doc" fields are left alone.
"""

import json
from pathlib import Path

from merge_golden import read_rows

GOLDEN = Path("evals/golden.jsonl")
DOC = "amat-10k-fy2025"

rows = read_rows(GOLDEN)
added = 0
for row in rows:
    for ev in row["evidence"]:
        for opt in [ev, *ev.get("alternatives", [])]:
            if "doc" not in opt:
                opt["doc"] = DOC
                added += 1

with open(GOLDEN, "w", encoding="utf-8") as f:
    for row in rows:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
print(f"{GOLDEN}: {len(rows)} questions, added doc to {added} evidence locations")
