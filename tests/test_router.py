"""Rule based router test."""
from router import route_docs

DOCS = [{"doc_id": "fy25", "fiscal_year": 2025}, {"doc_id": "fy24", "fiscal_year": 2024}]


def test_no_year_goes_to_latest():
    assert route_docs("Why did gross margin increase?", DOCS) == ["fy25"]


def test_current_year_only():
    assert route_docs("What was the FY2025 gross margin?", DOCS) == ["fy25"]


def test_prior_year_includes_next_report():
    # FY2024 숫자는 FY2024 10-K에도, FY2025 10-K(비교 연도)에도 있다
    assert route_docs("What was diluted EPS in fiscal 2024?", DOCS) == ["fy24", "fy25"]


def test_explicit_document_plus_other_year():
    q = "In the FY2024 10-K, what did management expect, and how did revenue turn out in fiscal 2025?"
    assert route_docs(q, DOCS) == ["fy24", "fy25"]


def test_year_without_matching_report_searches_all():
    assert route_docs("What happened in fiscal 2019?", DOCS) == ["fy24", "fy25"]