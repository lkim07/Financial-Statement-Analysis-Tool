"""대시보드 요약 테스트: 지표와 워터폴 계산 (PDF, Milvus 없이)."""
import pytest
from summary import kpis, pdf_text, waterfall, why_question
from test_eps import FACTS


def test_kpis_from_the_income_statement():
    cards = {c["item"]: c for c in kpis(FACTS, 2025)}
    assert cards["net_income"]["current"] == 6998 and cards["net_income"]["change"] == -179
    assert cards["net_income"]["change_pct"] == pytest.approx(-2.5)
    assert cards["gross_margin"]["current"] == 48.7 and cards["gross_margin"]["prior"] == 47.5  # p30과 같은 값
    assert cards["gross_margin"]["computed"] is True
    assert cards["eps_diluted"]["change"] == pytest.approx(0.05)


def test_waterfall_walks_from_prior_to_current_net_income():
    wf = waterfall(FACTS, 2025)
    bars = wf["bars"]
    assert bars[0]["kind"] == "total" and bars[0]["end"] == 7177
    assert bars[-1]["kind"] == "total" and bars[-1]["end"] == 6998
    for before, after in zip(bars[1:-2], bars[2:-1]):
        assert after["start"] == before["end"]  # 계단: 앞 막대가 끝난 곳에서 시작
    assert bars[-2]["end"] == pytest.approx(6998)  # 마지막 원인 막대가 정확히 올해 순이익에 닿는다
    tax = bars[1]
    assert (tax["item"], tax["kind"], tax["start"], tax["end"]) == ("tax_provision", "down", 7177, 5879)
    assert tax["fact"]["chunk_id"] == "amat-10k-fy2025:c0155"


def test_restructuring_bar_points_at_this_years_row():
    bar = next(b for b in waterfall(FACTS, 2025)["bars"] if b["item"] == "restructuring")
    assert bar["fact"]["value"] == 181 and bar["kind"] == "down"


def test_pdf_text_matches_how_the_statement_prints_numbers():
    assert pdf_text(2273.0, "USD_millions") == "2,273"
    assert pdf_text(-792.0, "USD_millions") == "792"
    assert pdf_text(8.66, "USD_per_share") == "8.66"


def test_why_question():
    d = {"label": "Operating expenses: Research, development and engineering", "current": 3570, "prior": 3233}
    assert why_question(d, 2025) == "Why did research, development and engineering increase in fiscal 2025?"