"""Read company, form type and fiscal year from an SEC filing's cover page (Phase 9-2, decision 034).

Every 10-K starts with the same SEC cover page: "Form 10-K", "For the fiscal year ended <date>",
and the company name above "(Exact name of registrant as specified in its charter)".
Rules are enough here; the user can correct the result, and corrections are kept as feedback.

    uv run --project backend python backend/ingest/detect.py data/amat_10k_fy2025.pdf
"""

import re
import sys
from datetime import datetime

FORM_10K = re.compile(r"\bForm\s+10-K(/A)?\b", re.IGNORECASE)  # 줄바꿈("FORM\n10-K")도 \s+가 잡는다
PROXY = re.compile(r"\bSCHEDULE\s+14A\b", re.IGNORECASE)
FY_END = re.compile(r"for the fiscal year ended\s+([A-Z][a-z]+\s+\d{1,2},\s*\d{4})", re.IGNORECASE)
NAME_MARK = re.compile(r"\((Exact name of registrant|Name of Registrant)", re.IGNORECASE)
SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "plc", "holdings", "the"}


def cover_text(pdf, pages: int = 2) -> str:
    """표지는 보통 1쪽, 가끔 2쪽까지 이어진다."""
    return "\n".join(pdf[i].get_text() for i in range(min(pages, pdf.page_count)))


def detect_cover(text: str) -> dict:
    """표지 글에서 읽은 값과, 읽지 못한 항목 목록(missing)."""
    out = {"doc_type": None, "fiscal_year": None, "period_end": None, "company_name": None}
    form = FORM_10K.search(text)
    if form:
        out["doc_type"] = "10-K/A" if form.group(1) else "10-K"  # 정정 공시(/A)는 따로 구분한다
    elif PROXY.search(text):
        out["doc_type"] = "DEF 14A"

    if m := FY_END.search(text):
        date = datetime.strptime(re.sub(r"\s+", " ", m.group(1)), "%B %d, %Y")
        out["period_end"] = date.strftime("%Y-%m-%d")
        # 회계연도 = 끝나는 날의 연도. AMAT(10월 말)와 Lam(6월 말) 모두 이 규칙을 따른다.
        # 1~2월에 끝나는 소매업체 중에는 전년도 숫자를 붙이는 곳이 있다: 사용자가 고칠 수 있게 한다
        out["fiscal_year"] = date.year

    lines = [ln.strip() for ln in text.splitlines()]
    for i, line in enumerate(lines):
        if NAME_MARK.search(line):
            above = [ln for ln in lines[:i] if ln]  # 표시 바로 위의 비어 있지 않은 줄이 회사 이름
            out["company_name"] = above[-1] if above else None
            break

    out["missing"] = [k for k, v in out.items() if v is None]
    return out


def name_key(name: str) -> str:
    """'Applied Materials, Inc.' -> 'applied materials',  'LAM RESEARCH CORPORATION' -> 'lam research'"""
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return " ".join(w for w in words if w not in SUFFIXES)


def same_company(cover_name: str | None, folder_name: str) -> bool:
    """표지의 회사와 폴더의 회사가 같아 보이나. 못 읽었으면 판단하지 않는다(True)."""
    if not cover_name:
        return True
    a, b = name_key(cover_name), name_key(folder_name)
    return bool(a and b) and (a == b or a in b or b in a)


if __name__ == "__main__":
    import pymupdf

    for path in sys.argv[1:]:
        print(path, detect_cover(cover_text(pymupdf.open(path))))