"""Rule-based document router: which documents should a question search?

Two steps (decision 026):
1. Document type: annual report (10-K) or proxy statement (DEF 14A)?
   - compensation / board / shareholder-meeting words -> proxy
   - financial-statement words -> 10-K
   - both -> both types (e.g. "How did CEO pay compare with net income?")
   - neither -> 10-K (most questions are about the business)
2. Year, within each chosen type (decision 021):
   - "FY2024 10-K" named explicitly -> that report
   - 10-K: a year Y -> the Y and Y+1 reports (the next 10-K repeats Y, possibly recast)
   - proxy: a year Y -> the proxy covering fiscal Y
   - no year -> the latest document of that type
   - a year with no matching document -> all documents of that type

Years are looked up per type: a 10-K and a proxy can both cover fiscal 2025,
and one dict keyed by year would silently keep only one of them.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingest"))
from registry import load_sources  # noqa: E402

TEN_K, PROXY = "10-K", "DEF 14A"

EXPLICIT_DOC = re.compile(r"\b(?:fy\s?)?(20\d{2})\s+10-?k\b")
YEAR = re.compile(r"\b(?:fy|fiscal(?:\s+year)?)\s?(20\d{2})\b")

PROXY_WORDS = re.compile(
    r"\b(compensat\w*|salary|salaries|bonus\w*|pay ratio|say[- ]on[- ]pay|pay versus performance|"
    r"named executive\w*|neos?|ceo pay|executive pay|equity awards? to|perquisites?|"
    r"directors?|board|nominees?|independen\w*|committees?|annual meeting|shareholder proposals?|"
    r"stockholder proposals?|beneficial(?:ly)? own\w*|ratif\w*|audit fees|auditor fees|"
    r"proxy|def 14a)\b",
    re.IGNORECASE,
)
FINANCIAL_WORDS = re.compile(
    r"\b(revenue|net income|gross (?:margin|profit)|operating (?:income|margin)|eps|earnings per share|"
    r"cash flow|tax\w*|inventor\w*|segment|capital expenditures|repurchas\w*|dividends?|10-?k)\b",
    re.IGNORECASE,
)


def load_docs(company_id: str | None = None) -> list[dict]:
    """라우터가 고를 수 있는 문서: ready 문서만, company_id를 주면 그 회사 것만 (decision 033)."""
    return load_sources(company_id)


def doc_types(question: str) -> list[str]:
    """1단계: 어느 종류의 문서를 볼지."""
    proxy = bool(PROXY_WORDS.search(question))
    financial = bool(FINANCIAL_WORDS.search(question))
    if proxy and financial:
        return [TEN_K, PROXY]
    if proxy:
        return [PROXY]
    return [TEN_K]


def route_within_type(q: str, docs: list[dict], doc_type: str) -> list[str]:
    """2단계: 한 종류 안에서 연도로 고른다."""
    by_year = {d["fiscal_year"]: d["doc_id"] for d in docs if d["doc_type"] == doc_type}
    if not by_year:
        return []
    years = [int(y) for y in YEAR.findall(q)]
    chosen = set()
    if doc_type == TEN_K:
        chosen |= {by_year[int(y)] for y in EXPLICIT_DOC.findall(q) if int(y) in by_year}
    for y in years:
        # 10-K는 다음 해 리포트도 그해를 비교 연도로 싣는다. 프록시는 그해 보상만 다룬다
        for report_year in ((y, y + 1) if doc_type == TEN_K else (y,)):
            if report_year in by_year:
                chosen.add(by_year[report_year])
    if chosen:
        return sorted(chosen)
    if years or (doc_type == TEN_K and EXPLICIT_DOC.findall(q)):
        return sorted(by_year.values())  # 연도를 말했는데 맞는 문서가 없으면 그 종류 전부
    return [by_year[max(by_year)]]  # 연도가 없으면 그 종류의 최신 문서


def route_docs(question: str, docs: list[dict], types: list[str] | None = None) -> list[str]:
    """types: 문서 종류를 밖에서 정했으면 (LLM 분류기, decision 026) 그것을 쓴다. 없으면 규칙."""
    q = question.lower()
    chosen = []
    for t in types or doc_types(question):
        chosen += route_within_type(q, docs, t)
    return sorted(set(chosen))