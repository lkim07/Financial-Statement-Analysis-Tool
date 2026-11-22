"""브리지 테스트: 실제 FY2025 손익계산서로 손으로 푼 숙제와 같은 답이 나와야 한다."""
import copy

import pytest
from bridge import net_income_bridge, report_differences
from extract import extract_income_statement
from test_numbers import CHUNK

FACTS = extract_income_statement([CHUNK], "amat-10k-fy2025")


def test_bridge_adds_up_to_net_income_change():
    b = net_income_bridge(FACTS)
    assert (b["year"], b["prior_year"], b["change"]) == (2025, 2024, -179)
    assert sum(d["contribution"] for d in b["drivers"]) == -179


def test_largest_driver_is_tax():
    top = net_income_bridge(FACTS)["drivers"][0]
    assert top["item"] == "tax_provision" and top["contribution"] == -1298
    assert top["page"] == 47 and top["chunk_id"] == "amat-10k-fy2025:c0155"


def test_earlier_year_from_same_report():
    b = net_income_bridge(FACTS, year=2024)
    assert b["change"] == 7177 - 6856
    restructuring = next(d for d in b["drivers"] if d["item"] == "restructuring")
    assert restructuring["contribution"] == 0  # 두 해 모두 —


def test_bridge_refuses_numbers_that_do_not_add_up():
    broken = copy.deepcopy(FACTS)
    for f in broken:
        if f["item"] == "tax_provision" and f["period"] == 2025:
            f["value"] = 2237
    with pytest.raises(ValueError):
        net_income_bridge(broken)


def test_report_differences_flags_recast():
    other = copy.deepcopy(FACTS)
    for f in other:
        f["doc_id"] = "older-report"
        if f["item"] == "gross_profit" and f["period"] == 2024:
            f["value"] = 12_000
    assert report_differences(FACTS, other) == ["gross_profit 2024: 12,897 vs 12,000"]