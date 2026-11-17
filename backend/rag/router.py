"""Rule-based document router: which documents should a question search?

This is the simple baseline the Phase 7 router has to beat. It reads only the question text.

Rules:
1. "FY2024 10-K" / "2024 10-K" named explicitly -> that document.
2. A year mentioned ("FY2025", "fiscal 2024") -> the report for that year AND the next year's
   report, because a 10-K also shows the prior year (sometimes recast).
3. No year at all -> the latest report only (users usually mean "now").
The result is the union of rules 1 and 2, or rule 3 alone.
"""

import json
import re
from pathlib import Path

SOURCES = Path("data/sources.json")

EXPLICIT_DOC = re.compile(r"\b(?:fy\s?)?(20\d{2})\s+10-?k\b")
YEAR = re.compile(r"\b(?:fy|fiscal(?:\s+year)?)\s?(20\d{2})\b")


def load_docs() -> list[dict]:
    return json.loads(SOURCES.read_text(encoding="utf-8-sig"))


def route_docs(question: str, docs: list[dict]) -> list[str]:
    q = question.lower()
    by_year = {d["fiscal_year"]: d["doc_id"] for d in docs}

    chosen = {by_year[int(y)] for y in EXPLICIT_DOC.findall(q) if int(y) in by_year}
    for y in map(int, YEAR.findall(q)):
        for report_year in (y, y + 1):  # 그해 리포트와, 그해를 비교 연도로 싣는 다음 해 리포트
            if report_year in by_year:
                chosen.add(by_year[report_year])

    if chosen:
        return sorted(chosen)
    if YEAR.findall(q) or EXPLICIT_DOC.findall(q):
        return sorted(by_year.values())  # 연도를 말했는데 맞는 리포트가 없으면 전부 본다
    return [by_year[max(by_year)]]  # 연도가 없으면 최신 리포트