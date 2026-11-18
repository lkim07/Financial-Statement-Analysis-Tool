"""숫자 추출과 검산 테스트. PDF 없이 돈다 (CI용): 실제 p47 청크 텍스트를 그대로 붙여 넣었다."""
import pytest
from extract import check_income_statement, extract_income_statement, parse_number, parse_table

P47 = """**(In millions, except per share amounts)**
| Fiscal Year | 2025 | 2024 | 2023 |
| --- | --- | --- | --- |
| Net revenue | $ 28,368 | $ 27,176 | $ 26,517 |
| Cost of products sold | 14,560 | 14,279 | 14,133 |
| Gross profit | 13,808 | 12,897 | 12,384 |
| Operating expenses: |  |  |  |
| Research, development and engineering | 3,570 | 3,233 | 3,102 |
| Marketing and selling | 858 | 836 | 776 |
| General and administrative | 910 | 961 | 852 |
| Restructuring charges | 181 | — | — |
| Total operating expenses | 5,519 | 5,030 | 4,730 |
| Income from operations | 8,289 | 7,867 | 7,654 |
| Interest expense | 269 | 247 | 238 |
| Interest and other income (expense), net | 1,251 | 532 | 300 |
| Income before income taxes | 9,271 | 8,152 | 7,716 |
| Provision for income taxes | 2,273 | 975 | 860 |
| Net income | $ 6,998 | $ 7,177 | $ 6,856 |
| Earnings per share: |  |  |  |
| Basic | $ 8.71 | $ 8.68 | $ 8.16 |
| Diluted | $ 8.66 | $ 8.61 | $ 8.11 |
| Weighted average number of shares: |  |  |  |
| Basic | 804 | 827 | 840 |
| Diluted | 808 | 834 | 845 |"""

CHUNK = {"chunk_id": "amat-10k-fy2025:c0155", "page": 47, "type": "table", "section": "body",
         "heading": "CONSOLIDATED STATEMENTS OF OPERATIONS", "text": P47}


@pytest.mark.parametrize("cell, expected", [
    ("$ 28,368", 28368), ("(792)", -792), ("—", 0), ("48.7 %", 48.7), ("$ 8.66", 8.66), ("", None), ("Basic", None),
])
def test_parse_number(cell, expected):
    assert parse_number(cell) == expected


def test_parse_table_reads_years_and_sections():
    rows = parse_table(P47)
    lookup = {(r["label"], r["period"]): r["value"] for r in rows}
    assert lookup[("Net revenue", 2025)] == 28368
    assert lookup[("Operating expenses: Restructuring charges", 2024)] == 0
    # 섹션이 Total에서 닫혀야 한다
    assert ("Income from operations", 2025) in lookup
    # Basic이 두 번 나오지만 섹션으로 구분된다
    assert lookup[("Earnings per share: Basic", 2025)] == 8.71
    assert lookup[("Weighted average number of shares: Basic", 2025)] == 804


def test_extract_maps_every_row():
    facts = extract_income_statement([CHUNK], "amat-10k-fy2025")
    assert len(facts) == 18 * 3
    tax = next(f for f in facts if f["item"] == "tax_provision" and f["period"] == 2025)
    assert tax["value"] == 2273 and tax["chunk_id"] == "amat-10k-fy2025:c0155"


def test_checks_pass_on_real_statement():
    assert check_income_statement(extract_income_statement([CHUNK], "amat-10k-fy2025")) == []


def test_checks_catch_a_wrong_number():
    facts = extract_income_statement([CHUNK], "amat-10k-fy2025")
    for f in facts:
        if f["item"] == "tax_provision" and f["period"] == 2025:
            f["value"] = 2237  # 자릿수 바뀜
    errors = check_income_statement(facts)
    assert len(errors) == 1 and "net_income" in errors[0]


def test_fy2024_severance_label_maps_to_restructuring():
    text = """| Fiscal Year | 2024 | 2023 | 2022 |
| --- | --- | --- | --- |
| Operating expenses: |  |  |  |
| Research, development and engineering | 3,233 | 3,102 | 2,771 |
| Marketing and selling | 836 | 776 | 703 |
| General and administrative | 961 | 852 | 735 |
| Severance and related charges | — | — | (4) |
| Total operating expenses | 5,030 | 4,730 | 4,205 |"""
    chunk = {**CHUNK, "chunk_id": "amat-10k-fy2024:c0001", "page": 52, "text": text}
    facts = extract_income_statement([chunk], "amat-10k-fy2024")
    sev = next(f for f in facts if f["item"] == "restructuring" and f["period"] == 2022)
    assert sev["value"] == -4 and sev["label"] == "Operating expenses: Severance and related charges"