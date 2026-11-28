"""Cover page detection (Phase 9-2). Real cover text patterns from AMAT and Lam 10-Ks and the AMAT proxy."""

from detect import detect_cover, name_key, same_company

AMAT = """UNITED STATES SECURITIES AND EXCHANGE COMMISSION
Form 10-K
For the fiscal year ended October 26, 2025
Commission file number 000-06920
Applied Materials, Inc.
(Exact name of registrant as specified in its charter)
"""
LAM = """FORM
10-K
For the fiscal year ended June 29, 2025
Commission file number: 0-12933
LAM RESEARCH CORPORATION
(Exact name of registrant as specified in its charter)
"""
PROXY = """SCHEDULE 14A INFORMATION
Definitive Proxy Statement
Applied Materials, Inc.

(Name of Registrant as Specified In Its Charter)
"""


def test_amat_cover():
    assert detect_cover(AMAT) == {"doc_type": "10-K", "fiscal_year": 2025, "period_end": "2025-10-26",
                                  "company_name": "Applied Materials, Inc.", "missing": []}


def test_form_split_over_two_lines_and_june_year_end():
    d = detect_cover(LAM)
    assert d["doc_type"] == "10-K" and d["fiscal_year"] == 2025 and d["period_end"] == "2025-06-29"
    assert d["company_name"] == "LAM RESEARCH CORPORATION"


def test_proxy_is_not_a_10k_and_has_no_fiscal_year_line():
    d = detect_cover(PROXY)
    assert d["doc_type"] == "DEF 14A" and d["company_name"] == "Applied Materials, Inc."
    assert set(d["missing"]) == {"fiscal_year", "period_end"}


def test_amendment_is_marked():
    assert detect_cover(AMAT.replace("Form 10-K", "Form 10-K/A"))["doc_type"] == "10-K/A"


def test_company_names_match_loosely():
    assert name_key("Applied Materials, Inc.") == "applied materials"
    assert same_company("LAM RESEARCH CORPORATION", "Lam Research")
    assert not same_company("LAM RESEARCH CORPORATION", "Applied Materials")
    assert same_company(None, "Anything")  # 못 읽었으면 경고하지 않는다