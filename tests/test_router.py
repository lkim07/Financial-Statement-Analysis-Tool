"""두 축 문서 라우터 테스트: 문서 종류 -> 연도."""
from decompose import bridge_source
from router import doc_types, route_docs

DOCS = [{"doc_id": "fy25", "fiscal_year": 2025, "doc_type": "10-K"},
        {"doc_id": "fy24", "fiscal_year": 2024, "doc_type": "10-K"},
        {"doc_id": "proxy26", "fiscal_year": 2025, "doc_type": "DEF 14A"}]  # 프록시가 마지막: 덮어쓰기 함정


def test_no_year_goes_to_latest():
    assert route_docs("Why did gross margin increase?", DOCS) == ["fy25"]


def test_current_year_only():
    assert route_docs("What was the FY2025 gross margin?", DOCS) == ["fy25"]


def test_prior_year_includes_next_report():
    assert route_docs("What was diluted EPS in fiscal 2024?", DOCS) == ["fy24", "fy25"]


def test_explicit_document_plus_other_year():
    q = "In the FY2024 10-K, what did management expect, and how did revenue turn out in fiscal 2025?"
    assert route_docs(q, DOCS) == ["fy24", "fy25"]


def test_year_without_matching_report_searches_all():
    assert route_docs("What happened in fiscal 2019?", DOCS) == ["fy24", "fy25"]


def test_same_fiscal_year_does_not_overwrite():
    # 10-K와 프록시가 둘 다 fiscal 2025: 연도 딕셔너리 하나로 만들면 10-K가 사라진다
    assert route_docs("What was net revenue in fiscal 2025?", DOCS) == ["fy25"]
    assert bridge_source("Why did net income decrease in FY2025?", DOCS) == ("fy25", 2025)


def test_compensation_questions_go_to_the_proxy():
    assert doc_types("What was the CEO's total compensation for fiscal 2025?") == ["DEF 14A"]
    assert route_docs("What was the CEO's total compensation for fiscal 2025?", DOCS) == ["proxy26"]
    assert route_docs("How many directors are nominated for election?", DOCS) == ["proxy26"]


def test_mixed_questions_search_both_types():
    q = "How did CEO compensation compare with net income in fiscal 2025?"
    assert doc_types(q) == ["10-K", "DEF 14A"]
    assert route_docs(q, DOCS) == ["fy25", "proxy26"]