"""질문 분해 테스트: 원인 선택, 리포트 선택, 라운드로빈 병합."""
from bridge import net_income_bridge
from decompose import (bridge_source, driver_query, is_profit_change_question, merge_seats, plan,
                       select_drivers)
from extract import extract_income_statement
from test_numbers import CHUNK

FACTS = extract_income_statement([CHUNK], "amat-10k-fy2025")
REGISTRY = [{"doc_id": "amat-10k-fy2025", "fiscal_year": 2025}, {"doc_id": "amat-10k-fy2024", "fiscal_year": 2024}]


def test_detects_profit_change_questions():
    assert is_profit_change_question("Revenue and operating income both increased in FY2025. Why did net income decrease?")
    assert is_profit_change_question("Why did net income increase in fiscal 2024 compared with fiscal 2023?")
    assert not is_profit_change_question("Why did gross margin increase?")
    assert not is_profit_change_question("Net income decreased, so why did diluted EPS increase?")


def test_bridge_source_uses_the_year_report():
    assert bridge_source("Why did net income decrease in FY2025?", REGISTRY) == ("amat-10k-fy2025", 2025)
    assert bridge_source("Why did net income increase in fiscal 2024?", REGISTRY) == ("amat-10k-fy2024", 2024)
    assert bridge_source("Why did net income decrease?", REGISTRY) == ("amat-10k-fy2025", 2025)
    # FY2023 리포트는 없다 -> FY2024 리포트의 비교 연도
    assert bridge_source("Why did net income rise in fiscal 2023?", REGISTRY) == ("amat-10k-fy2024", 2023)


def test_coverage_80_fy2025_picks_three():
    drivers = select_drivers(net_income_bridge(FACTS, 2025), 0.8)
    assert [d["item"] for d in drivers] == ["tax_provision", "gross_profit", "other_income_net"]


def test_coverage_80_fy2024_picks_four():
    drivers = select_drivers(net_income_bridge(FACTS, 2024), 0.8)
    assert [d["item"] for d in drivers] == ["gross_profit", "other_income_net", "rd_e", "tax_provision"]


def test_max_drivers_caps_the_list():
    assert len(select_drivers(net_income_bridge(FACTS, 2025), 0.99, max_drivers=4)) == 4


def test_query_direction_follows_the_item_not_the_contribution():
    tax = select_drivers(net_income_bridge(FACTS, 2025), 0.3)[0]
    assert tax["contribution"] < 0 and "increased" in driver_query(tax)  # 세금이 늘었다 (기여는 -)


def test_plan_skips_other_questions():
    assert plan("Why did gross margin increase?", REGISTRY, facts_loader=lambda d: FACTS) is None
    p = plan("Why did net income decrease in FY2025?", REGISTRY, facts_loader=lambda d: FACTS)
    assert [d["item"] for d in p["drivers"]] == ["tax_provision", "gross_profit", "other_income_net"]
    assert p["statement_chunks"] == ["amat-10k-fy2025:c0155"]  # 손익계산서 표: 고정석


def test_seats_fixed_then_original_then_drivers():
    original = ["o1", "o2", "o3", "o4", "o5", "o6"]
    drivers = [["t1", "t2"], ["o1", "g2"], ["x1"]]  # 두 번째 원인의 1등은 원래 질문과 겹친다
    merged = merge_seats(original, drivers, budget=8, fixed=["s1"], keep_original=3)
    assert merged[:4] == ["s1", "o1", "o2", "o3"]   # 고정석, 원래 질문 상위 3개
    assert merged[4:7] == ["t1", "g2", "x1"]        # 원인마다 1등 (겹치면 다음 것)
    assert merged[7] == "o4"                        # 남는 자리는 원래 질문부터 돌아가며
    assert len(set(merged)) == 8


def test_seats_respect_budget():
    assert len(merge_seats(["o1", "o2"], [["t1"], ["g1"]], budget=2, fixed=["s1", "s2", "s3"])) == 2