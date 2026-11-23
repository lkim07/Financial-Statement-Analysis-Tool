"""현금흐름표 추출, 재무제표 간 검산, EPS 브리지, EPS 분해 테스트."""
import pytest
from bridge import eps_bridge
from decompose import is_eps_change_question, plan
from extract import check_cross_statement, extract_cash_flow, extract_income_statement
from grounding import computed_source
from test_numbers import CHUNK

# FY2025 10-K p51 현금흐름표 일부 (LlamaParse 표기)
P51 = """| Fiscal Year | 2025 | 2024 | 2023 |
| --- | --- | --- | --- |
| Cash flows from operating activities: |  |  |  |
| Net income | $ 6,998 | $ 7,177 | $ 6,856 |
| Adjustments required to reconcile net income to cash provided by operating activities: |  |  |  |
| Depreciation and amortization | 435 | 392 | 515 |
| (Gain) loss and impairments on investments, net | (792) | (15) | (16) |
| Cash provided by operating activities | 7,958 | 8,677 | 8,700 |
| Cash flows from financing activities: |  |  |  |
| Proceeds from common stock issuances | 261 | 243 | 227 |
| Common stock repurchases | (4,895) | (3,823) | (2,189) |
| Payments of dividends to stockholders | (1,384) | (1,192) | (975) |"""
CF_CHUNK = {"chunk_id": "amat-10k-fy2025:c0160", "page": 51, "type": "table", "section": "body",
            "heading": "CONSOLIDATED STATEMENTS OF CASH FLOWS — (Continued)", "text": P51}

DOC = "amat-10k-fy2025"
FACTS = extract_income_statement([CHUNK], DOC) + extract_cash_flow([CF_CHUNK], DOC)
REGISTRY = [{"doc_id": DOC, "fiscal_year": 2025}, {"doc_id": "amat-10k-fy2024", "fiscal_year": 2024}]


def test_cash_flow_keeps_only_mapped_rows_through_nested_sections():
    cf = {(f["item"], f["period"]): f["value"] for f in FACTS if f["statement"] == "cash_flow"}
    assert cf[("share_repurchases", 2025)] == -4895
    assert cf[("investment_gains_losses", 2025)] == -792      # 섹션 두 겹 아래의 행
    assert cf[("operating_cash_flow", 2024)] == 8677
    assert not any(item == "Proceeds from common stock issuances" for item, _ in cf)  # 목록에 없는 행은 건너뜀


def test_net_income_agrees_across_statements():
    assert check_cross_statement(FACTS) == []
    broken = [dict(f, value=6989) if f["statement"] == "cash_flow" and f["item"] == "net_income"
              and f["period"] == 2025 else f for f in FACTS]
    assert check_cross_statement(broken) == [f"{DOC} 2025 net_income: income statement 6,998 vs cash flow 6,989"]


def test_eps_bridge_matches_homework():
    b = eps_bridge(FACTS)
    e = b["effects"]
    # 숙제: 순이익 먼저 -0.2146, 주식 수 나중 +0.2700
    assert e["net_income_first"]["net_income"] == pytest.approx(-0.2146, abs=1e-4)
    assert e["net_income_first"]["shares"] == pytest.approx(0.2700, abs=1e-4)
    # 순서를 바꾸면 몫이 달라진다 (교차항 -0.0069를 누가 가져가느냐)
    assert e["shares_first"]["net_income"] == pytest.approx(-0.2215, abs=1e-4)
    assert e["shares_first"]["shares"] == pytest.approx(0.2769, abs=1e-4)
    # 평균(Shapley)을 쓴다. 합은 어느 쪽이든 같다
    assert e["net_income"] + e["shares"] == pytest.approx(b["computed_change"])
    assert b["computed_change"] == pytest.approx(0.0554, abs=1e-4)
    assert b["reported_change"] == pytest.approx(0.05)
    assert b["repurchases"] == 4895
    assert b["chunk_ids"] == ["amat-10k-fy2025:c0155", "amat-10k-fy2025:c0160"]


def test_how_much_questions_are_not_decomposed():
    # q50: 숫자 질문. 분해하면 고정석이 원래 질문의 자리를 빼앗아 1.00 -> 0.00이 됐다
    q50 = "What was diluted EPS in fiscal 2024, and how much did it grow from fiscal 2023?"
    assert not is_eps_change_question(q50)
    assert plan(q50, REGISTRY, facts_loader=lambda d: FACTS) is None
    assert not is_eps_change_question("How much did net income per share change?")


def test_eps_questions_get_an_eps_plan():
    q = "Net income decreased, so why did diluted EPS increase?"
    assert is_eps_change_question(q)
    p = plan(q, REGISTRY, facts_loader=lambda d: FACTS)
    assert p["kind"] == "eps"
    assert [d["item"] for d in p["drivers"]] == ["shares_diluted", "tax_provision"]
    assert "decreased primarily due to repurchases" in p["drivers"][0]["query"]
    assert p["statement_chunks"] == ["amat-10k-fy2025:c0155", "amat-10k-fy2025:c0160"]


def test_eps_computed_source():
    p = plan("Why did diluted EPS increase in FY2025?", REGISTRY, facts_loader=lambda d: FACTS)
    src = computed_source(p, facts_loader=lambda d: FACTS, label_of=lambda d: "FY2025 10-K")
    assert src["chunk_id"] == "calc:eps:amat-10k-fy2025:2025"
    assert "Diluted EPS: 8.61 -> 8.66 (reported change +0.05)." in src["text"]
    assert "contribution -0.22 per share" in src["text"] and "contribution +0.27 per share" in src["text"]
    assert "834 -> 808 million (-3.1%)" in src["text"] and "(-2.5%)" in src["text"]
    assert "4,895 USD millions" in src["text"]