"""A second company's statements (Phase 9-3): Lam Research FY2025 10-K, chunks exactly as LlamaParse produced them.

Traps: heading replaced by the unit line, header row merged with the first data row, different labels,
three cost-of-goods rows, 'Gross margin' in dollars, tax in parentheses, amounts in thousands.
"""

from bridge import net_income_bridge
from extract import (check_income_statement, extract_document, extract_income_statement, parse_table,
                     repair_header, split_row)

H = ("| June 29, June 30, June 25, 2025 2024 2023<br/>Revenue | Year Ended<br/>June 29, June 30, June 25, 2025 2024 "
     "2023<br/>$ 18,435,591 | Year Ended<br/>June 29, June 30, June 25, 2025 2024 2023<br/>$ 14,905,386 | "
     "Year Ended<br/>June 29, June 30, June 25, 2025 2024 2023<br/>$ 17,428,516 |\n| --- | --- | --- | --- |\n")
PART1 = H + """| Cost of goods sold | 9,456,532 | 7,809,220 | 9,573,425 |
| Restructuring charges, net - cost of goods sold | — | 43,375 | 78,166 |
| Total cost of goods sold | 9,456,532 | 7,852,595 | 9,651,591 |
| Gross margin | 8,979,059 | 7,052,791 | 7,776,925 |
| Research and development | 2,096,387 | 1,902,444 | 1,727,162 |
| Selling, general, and administrative | 981,704 | 868,247 | 832,753 |
| Restructuring charges, net - operating expenses | — | 18,187 | 42,150 |
| Total operating expenses | 3,078,091 | 2,788,878 | 2,602,065 |
| Operating income | 5,900,968 | 4,263,913 | 5,174,860 |
| Other income (expense), net | 57,161 | 96,309 | (65,650) |
| Income before income taxes | 5,958,129 | 4,360,222 | 5,109,210 |
| Income tax expense | (599,912) | (532,450) | (598,279) |"""
PART2 = H + """| Net income | $ 5,358,217 | $ 3,827,772 | $ 4,510,931 |
| Net income per share: |  |  |  |
| Basic | $ 4.17 | $ 2.91 | $ 3.33 |
| Diluted | $ 4.15 | $ 2.90 | $ 3.32 |
| Number of shares used in per share calculations: |  |  |  |
| Basic | 1,286,101 | 1,314,102 | 1,354,715 |
| Diluted | 1,290,142 | 1,319,949 | 1,358,336 |"""
EPS_NOTE = """| | 2025 | 2024 |
| --- | --- | --- |
| Net income | 5,358,217 | 3,827,772 |
| Basic | 1,286,101 | 1,314,102 |
| Diluted | 1,290,142 | 1,319,949 |"""
UNIT = "(in thousands, except per share data)"


def chunk(i, text, page=38, kind="table"):
    return {"chunk_id": f"lrcx-10k-fy2025:c{i:04d}", "doc_id": "lrcx-10k-fy2025", "type": kind, "page": page,
            "heading": UNIT, "text": text, "section": "body"}


CHUNKS = [chunk(169, PART1), chunk(170, PART2), chunk(171, "See Notes", kind="text"), chunk(300, EPS_NOTE, page=70)]


def test_merged_header_gives_years_and_the_lost_first_row():
    years, first = repair_header(split_row(H.splitlines()[0]))
    assert years == [2025, 2024, 2023]
    assert first[0] == "Revenue" and first[1] == "$ 18,435,591"
    rows = parse_table(PART1)
    assert {"label": "Revenue", "period": 2025, "value": 18435591.0, "percent": False} in rows


def test_found_by_content_when_heading_is_the_unit_line():
    facts = extract_income_statement(CHUNKS, "lrcx-10k-fy2025")
    v = {(f["item"], f["period"]): f for f in facts}
    assert v[("net_revenue", 2025)]["value"] == 18435591
    assert v[("cost_of_products_sold", 2024)]["value"] == 7852595  # Total 줄이 매출원가 (함정 ②)
    assert v[("gross_profit", 2025)]["value"] == 8979059  # 'Gross margin' 달러 금액 (함정 ③)
    assert v[("sg_a", 2025)]["value"] == 981704
    assert v[("tax_provision", 2025)]["value"] == 599912  # 괄호 -> 비용 양수 (함정 ④)
    assert v[("net_revenue", 2025)]["unit"] == "USD_thousands"  # (함정 ⑤)
    assert v[("shares_basic", 2025)]["unit"] == "shares_thousands"
    assert v[("eps_diluted", 2025)]["unit"] == "USD_per_share"
    assert all(f["page"] == 38 for f in facts)  # 주석의 EPS 표(p70)는 고르지 않았다


def test_all_checks_pass_for_all_three_years():
    facts, errors, gaps = extract_document("lrcx-10k-fy2025", CHUNKS)
    assert errors == []
    assert gaps == ["cash flow not found"]  # 이 테스트 묶음에는 현금흐름표가 없다: 조용히 넘어가지 않는다
    assert sorted({f["period"] for f in facts}) == [2023, 2024, 2025]


def test_wrong_cost_row_would_be_caught():
    """'Cost of goods sold'(구조조정 제외)를 매출원가로 쓰면 FY2024 검산이 깨진다. FY2025만 봤다면 몰랐다."""
    facts = extract_income_statement(CHUNKS, "lrcx-10k-fy2025")
    for f in facts:
        if f["item"] == "cost_of_products_sold":
            f["value"] = next(g["value"] for g in facts if g["item"] == "cost_of_goods_sold_before_restructuring"
                              and g["period"] == f["period"])
    errors = check_income_statement(facts)
    assert any("2024 gross_profit" in e for e in errors) and not any("2025 gross_profit" in e for e in errors)


def test_lam_bridge_adds_up():
    facts = extract_income_statement(CHUNKS, "lrcx-10k-fy2025")
    b = net_income_bridge(facts, 2025)
    items = [d["item"] for d in b["drivers"]]
    assert "sg_a" in items and "marketing_selling" not in items  # 없는 항목은 0 막대로 그리지 않는다


CF1 = """| | 2025 | 2024 |
| --- | --- | --- |
| Net income | $ 5,358,217 | $ 3,827,772 |
| Depreciation and amortization | 386,277 | 359,699 |
| Equity-based compensation expense | 343,371 | 293,058 |
| Net cash provided by operating activities | 6,173,264 | 4,652,269 |
| Capital expenditures and intangible assets | (759,186) | (396,670) |"""
CF2 = """| | 2025 | 2024 |
| --- | --- | --- |
| Treasury stock purchases | (3,422,321) | (2,842,807) |
| Dividends paid | (1,149,542) | (1,018,915) |
| Net cash used for financing activities | (4,936,643) | (3,995,850) |"""


def test_cash_flow_continues_on_the_next_page():
    """재무활동 부분은 다음 쪽에 있고 항목이 3개뿐이다. 첫 쪽에 이어지면 같이 읽는다."""
    from extract import extract_cash_flow
    cf = [{**chunk(400, CF1, page=41), "heading": "(in thousands)"}, {**chunk(401, CF2, page=42), "heading": ""}]
    v = {(f["item"], f["period"]): f["value"] for f in extract_cash_flow(cf, "lrcx-10k-fy2025")}
    assert v[("share_repurchases", 2025)] == -3422321 and v[("operating_cash_flow", 2025)] == 6173264


# Lam 현금흐름표 청크 그대로: 머리가 두 줄('Year Ended' + 날짜)이고, 쪼개진 뒤 청크에는 날짜 줄이 없다
Y1 = "|  | Year Ended |  |  |\n| --- | --- | --- | --- |\n"
DATES = "|  | June 29, 2025 | June 30, 2024 | June 25, 2023 |\n"
C178 = Y1 + DATES + """| CASH FLOWS FROM OPERATING ACTIVITIES: |  |  |  |
| Net income | $ 5,358,217 | $ 3,827,772 | $ 4,510,931 |
| Adjustments to reconcile net income to net cash provided by operating activities: |  |  |  |
| Depreciation and amortization | 386,277 | 359,699 | 342,432 |
| Deferred income taxes | (363,247) | (198,981) | (172,061) |
| Equity-based compensation expense | 343,371 | 293,058 | 286,600 |
| Net cash provided by operating activities | 6,173,264 | 4,652,269 | 5,178,938 |
| CASH FLOWS FROM INVESTING ACTIVITIES: |  |  |  |
| Capital expenditures and intangible assets | (759,186) | (396,670) | (501,568) |"""
C179 = Y1 + """| CASH FLOWS FROM INVESTING ACTIVITIES: |  |  |  |
| Other, net | 51,094 | (11,710) | (11,171) |
| Net cash used for investing activities | (708,092) | (370,614) | (534,562) |"""
C180 = Y1 + DATES + """| CASH FLOWS FROM FINANCING ACTIVITIES: |  |  |  |
| Treasury stock purchases | (3,422,321) | (2,842,807) | (2,017,012) |
| Dividends paid | (1,149,542) | (1,018,915) | (907,907) |
| Net cash used for financing activities | (4,936,643) | (3,995,850) | (2,830,667) |"""


def test_lam_cash_flow_two_row_header_and_split_chunks():
    from extract import check_cross_statement, extract_cash_flow, table_years
    assert table_years(C178) == [2025, 2024, 2023]
    assert table_years(C179) is None  # 뒤 청크에는 연도가 없다
    cf = [{**chunk(i, t, page=pg), "heading": "(in thousands)"}
          for i, t, pg in [(178, C178, 41), (179, C179, 41), (180, C180, 42)]]
    facts = extract_cash_flow(cf, "lrcx-10k-fy2025")
    v = {(f["item"], f["period"]): f["value"] for f in facts}
    assert v[("investing_cash_flow", 2025)] == -708092  # 앞 청크의 연도를 이어받았다
    assert v[("share_repurchases", 2024)] == -2842807
    assert v[("capital_expenditures", 2023)] == -501568
    assert {f["unit"] for f in facts} == {"USD_thousands"}
    income = extract_income_statement(CHUNKS, "lrcx-10k-fy2025")
    assert check_cross_statement(income + facts) == []  # 두 표의 순이익이 같다


def test_full_lam_document_has_no_gaps():
    from extract import extract_document
    cf = [{**chunk(i, t, page=pg), "heading": "(in thousands)"}
          for i, t, pg in [(178, C178, 41), (179, C179, 41), (180, C180, 42)]]
    _, errors, gaps = extract_document("lrcx-10k-fy2025", CHUNKS + cf)
    assert errors == [] and gaps == []


def test_missing_rows_are_named():
    from extract import check_coverage
    facts = extract_income_statement(CHUNKS, "lrcx-10k-fy2025")
    facts = [f for f in facts if not (f["item"] == "eps_diluted" and f["period"] == 2025)]
    gaps = check_coverage(facts)
    assert "income statement missing eps_diluted 2025" in gaps[0] and gaps[1] == "cash flow not found"