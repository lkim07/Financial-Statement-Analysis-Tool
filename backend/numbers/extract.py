"""재무제표 표 청크 -> (doc_id, statement, item, period, value) 숫자 테이블.

원칙: 숫자는 LLM이 아니라 코드가 읽고 계산한다. 여기서 뽑은 숫자로 7-2의 브리지(변동 분해)를 만든다.

사용법:
    uv run --project backend python backend/numbers/extract.py --doc amat-10k-fy2025 --doc amat-10k-fy2024

입력: data/chunks/<doc_id>/llamaparse.jsonl 의 표 청크 (LlamaParse 마크다운 표)
출력: data/numbers.db (SQLite) 의 facts 테이블
대상: 손익계산서(income_statement), 현금흐름표(cash_flow: 필요한 행만)
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingest"))
from registry import chunks_path, get_doc  # noqa: E402

DB_PATH = Path("data/numbers.db")
YEAR = re.compile(r"^(?:fy\s?)?(20\d{2})$", re.IGNORECASE)

# 손익계산서 원문 라벨 -> 정규화한 키.
# 키를 따로 두는 이유: 해마다(또는 회사마다) 라벨 표현이 조금씩 달라도 같은 항목으로 비교하려고.
# 섹션 아래 행은 "섹션: 라벨"로 들어온다 (Basic이 EPS에도, 주식 수에도 있어서).
INCOME_STATEMENT_ITEMS = {
    "Net revenue": "net_revenue",
    "Cost of products sold": "cost_of_products_sold",
    "Gross profit": "gross_profit",
    "Operating expenses: Research, development and engineering": "rd_e",
    "Operating expenses: Marketing and selling": "marketing_selling",
    "Operating expenses: General and administrative": "general_admin",
    "Operating expenses: Restructuring charges": "restructuring",
    # FY2024 10-K는 같은 성격의 비용을 이 이름으로 적었다 (2022년 (4) = 이전 충당금 환입).
    # 같은 키로 묶어야 해마다 비교할 수 있다. 원문 라벨은 label 칸에 그대로 남는다.
    "Operating expenses: Severance and related charges": "restructuring",
    "Operating expenses: Total operating expenses": "total_operating_expenses",
    "Income from operations": "operating_income",
    "Interest expense": "interest_expense",
    "Interest and other income (expense), net": "other_income_net",
    "Income before income taxes": "pretax_income",
    "Provision for income taxes": "tax_provision",
    "Net income": "net_income",
    "Earnings per share: Basic": "eps_basic",
    "Earnings per share: Diluted": "eps_diluted",
    "Weighted average number of shares: Basic": "shares_basic",
    "Weighted average number of shares: Diluted": "shares_diluted",
}

# 섹션 라벨 행("Operating expenses:")은 다음 소계/합계 행에서 끝나야 한다.
# 이 라벨이 나오면 섹션을 닫는다: Total operating expenses 다음의 Income from operations는 섹션 밖이다.
SECTION_ENDS = {"Total operating expenses"}

# 현금흐름표는 행이 40개가 넘는다. 브리지와 질문에 쓰는 행만 고르고 나머지는 조용히 건너뛴다.
# 섹션 라벨이 여러 겹이라("Cash flows from operating activities:" 아래 "Adjustments ...:")
# 섹션을 뺀 맨 끝 라벨로 찾는다. 현금흐름표에는 같은 이름의 행이 두 번 나오지 않는다.
CASH_FLOW_ITEMS = {
    "Net income": "net_income",
    "Depreciation and amortization": "depreciation_amortization",
    "Deferred income taxes": "deferred_income_taxes",
    "(Gain) loss and impairments on investments, net": "investment_gains_losses",
    "Share-based compensation": "share_based_compensation",
    "Cash provided by operating activities": "operating_cash_flow",
    "Capital expenditures": "capital_expenditures",
    "Cash used in investing activities": "investing_cash_flow",
    "Common stock repurchases": "share_repurchases",
    "Payments of dividends to stockholders": "dividends_paid",
    "Cash used in financing activities": "financing_cash_flow",
}

UNITS = {"eps_basic": "USD_per_share", "eps_diluted": "USD_per_share",
         "shares_basic": "shares_millions", "shares_diluted": "shares_millions"}


def parse_number(cell: str) -> float | None:
    """표 칸 하나를 숫자로. 숫자가 아니면 None.

    "$ 28,368" -> 28368   "(792)" -> -792   "—" -> 0   "48.7 %" -> 48.7   "" -> None
    """
    s = cell.replace("$", "").replace(",", "").replace("%", "").replace("\xa0", " ").strip()
    if s in {"—", "–", "-"}:  # 대시는 "해당 금액 없음" = 0
        return 0.0
    negative = s.startswith("(") and s.endswith(")")  # 회계 표기: 괄호 = 음수
    s = s.strip("()").strip()
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


def split_row(line: str) -> list[str]:
    """'| a | b | c |' -> ['a', 'b', 'c']"""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_table_line(line: str) -> bool:
    return line.strip().startswith("|")


def is_separator(cells: list[str]) -> bool:
    return all(set(c) <= set("-: ") for c in cells)


def find_years(cells: list[str]) -> list[int] | None:
    """헤더 행이면 열마다 연도를 돌려준다: ['Fiscal Year','2025','2024','2023'] -> [2025, 2024, 2023]"""
    years = [YEAR.match(c.strip()) for c in cells[1:]]
    if years and all(years):
        return [int(m.group(1)) for m in years]
    return None


def clean_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.replace("*", "")).strip()


def parse_table(text: str) -> list[dict]:
    """마크다운 표 하나 -> [{"label", "period", "value"}, ...]

    - 헤더 행에서 연도 열을 찾는다. 연도를 못 찾으면 빈 목록 (연도 표가 아니다).
    - 값이 하나도 없는 행("Operating expenses:")은 섹션 라벨: 아래 행 라벨 앞에 붙인다.
    """
    years = None
    section = ""
    rows = []
    for line in text.splitlines():
        if not is_table_line(line):
            continue
        cells = split_row(line)
        if is_separator(cells):
            continue
        if years is None:
            years = find_years(cells)
            continue
        label, values = clean_label(cells[0]), cells[1:]
        if not label:
            continue
        numbers = [parse_number(v) for v in values]
        if all(n is None for n in numbers):  # 섹션 라벨 행
            section = label.rstrip(":") + ":"
            continue
        full_label = f"{section} {label}" if section else label
        for period, value in zip(years, numbers):
            if value is not None:
                rows.append({"label": full_label, "period": period, "value": value})
        if label in SECTION_ENDS:
            section = ""
    return rows


def find_statement_chunks(chunks: list[dict], heading: str) -> list[dict]:
    """제목이 heading인 표 청크들.

    표가 행 단위로 쪼개졌을 수 있고, 다음 페이지로 이어질 수도 있다 ("— (Continued)").
    첫 페이지부터 페이지가 끊기지 않고 이어지는 동안의 청크를 전부 돌려준다.
    (재무제표 본표만 잡고, 한참 뒤 주석에 같은 제목이 나와도 섞이지 않게)
    """
    matches = [c for c in chunks
               if c["type"] == "table" and heading.lower() in (c.get("heading") or "").lower()
               and c.get("section", "body") == "body"]
    if not matches:
        return []
    pages = sorted({c["page"] for c in matches})
    run = [pages[0]]
    for p in pages[1:]:
        if p != run[-1] + 1:
            break
        run.append(p)
    return [c for c in matches if c["page"] in run]


def extract_statement(chunks: list[dict], doc_id: str, statement: str, heading: str,
                      items: dict[str, str], by_last_label: bool = False, warn: bool = True) -> list[dict]:
    """재무제표 하나 -> facts. items: 원문 라벨 -> 정규화한 키.

    by_last_label: 섹션 접두어를 떼고 맨 끝 라벨로 찾는다 (현금흐름표).
    warn: 키 목록에 없는 라벨을 알린다 (손익계산서는 모든 행을 써야 하니 켠다).
    """
    facts, unknown = [], set()
    for chunk in find_statement_chunks(chunks, heading):
        for row in parse_table(chunk["text"]):
            label = row["label"].split(": ")[-1] if by_last_label else row["label"]
            item = items.get(label)
            if item is None:
                unknown.add(row["label"])
                continue
            facts.append({
                "doc_id": doc_id, "statement": statement, "item": item,
                "label": row["label"], "period": row["period"], "value": row["value"],
                "unit": UNITS.get(item, "USD_millions"),
                "page": chunk["page"], "chunk_id": chunk["chunk_id"],
            })
    if warn:
        for label in sorted(unknown):
            print(f"  [warn] {doc_id}: unmapped label '{label}'")
    # 표가 쪼개지며 헤더 행이 반복되면 같은 숫자가 두 번 나올 수 있다: (item, period)로 하나만 남긴다
    unique = {(f["item"], f["period"]): f for f in facts}
    return list(unique.values())


def extract_income_statement(chunks: list[dict], doc_id: str) -> list[dict]:
    return extract_statement(chunks, doc_id, "income_statement", "STATEMENTS OF OPERATIONS", INCOME_STATEMENT_ITEMS)


def extract_cash_flow(chunks: list[dict], doc_id: str) -> list[dict]:
    return extract_statement(chunks, doc_id, "cash_flow", "STATEMENTS OF CASH FLOWS", CASH_FLOW_ITEMS,
                             by_last_label=True, warn=False)


# --- 검산 (합계 = 항목들의 합) ----------------------------------------------

# (결과 항목, [(부호, 항목), ...]): 결과 = 부호를 붙인 항목들의 합
INCOME_STATEMENT_CHECKS = [
    ("gross_profit", [(1, "net_revenue"), (-1, "cost_of_products_sold")]),
    ("total_operating_expenses", [(1, "rd_e"), (1, "marketing_selling"),
                                  (1, "general_admin"), (1, "restructuring")]),
    ("operating_income", [(1, "gross_profit"), (-1, "total_operating_expenses")]),
    ("pretax_income", [(1, "operating_income"), (-1, "interest_expense"), (1, "other_income_net")]),
    ("net_income", [(1, "pretax_income"), (-1, "tax_provision")]),
]
OPTIONAL = {"restructuring"}  # 없는 해에는 행 자체가 없을 수 있다 -> 0으로 본다


def check_cross_statement(facts: list[dict], tolerance: float = 1.0) -> list[str]:
    """손익계산서와 현금흐름표의 순이익이 같은가. 다르면 둘 중 하나를 잘못 읽은 것이다."""
    ni = {}
    for f in facts:
        if f["item"] == "net_income":
            ni.setdefault((f["doc_id"], f["period"]), {})[f["statement"]] = f["value"]
    errors = []
    for (doc_id, period), by_stmt in sorted(ni.items()):
        if len(by_stmt) == 2 and abs(by_stmt["income_statement"] - by_stmt["cash_flow"]) > tolerance:
            errors.append(f"{doc_id} {period} net_income: income statement {by_stmt['income_statement']:,.0f}"
                          f" vs cash flow {by_stmt['cash_flow']:,.0f}")
    return errors


def check_income_statement(facts: list[dict], tolerance: float = 1.0) -> list[str]:
    """검산이 틀린 곳을 문자열 목록으로. 반올림 때문에 1(백만 달러)까지는 허용한다."""
    facts = [f for f in facts if f.get("statement", "income_statement") == "income_statement"]
    values = {(f["doc_id"], f["period"], f["item"]): f["value"] for f in facts}
    errors = []
    for doc_id, period in sorted({(f["doc_id"], f["period"]) for f in facts}):
        def get(item):
            if (doc_id, period, item) in values:
                return values[(doc_id, period, item)]
            return 0.0 if item in OPTIONAL else None

        for total, parts in INCOME_STATEMENT_CHECKS:
            expected = get(total)
            part_values = [get(item) for _, item in parts]
            if expected is None or None in part_values:
                missing = [i for i in [total] + [p for _, p in parts] if get(i) is None]
                errors.append(f"{doc_id} {period} {total}: missing {', '.join(missing)}")
                continue
            computed = sum(sign * v for (sign, _), v in zip(parts, part_values))
            if abs(computed - expected) > tolerance:
                errors.append(f"{doc_id} {period} {total}: reported {expected:,.0f} vs computed {computed:,.0f}")
    return errors


# --- 저장 ---------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    doc_id    TEXT NOT NULL,
    statement TEXT NOT NULL,
    item      TEXT NOT NULL,
    label     TEXT NOT NULL,
    period    INTEGER NOT NULL,
    value     REAL NOT NULL,
    unit      TEXT NOT NULL,
    page      INTEGER NOT NULL,
    chunk_id  TEXT NOT NULL,
    PRIMARY KEY (doc_id, statement, item, period)
)
"""


def save(facts: list[dict], db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(SCHEMA)
        # 같은 문서를 다시 추출하면 덮어쓴다 (INSERT OR REPLACE)
        conn.executemany(
            "INSERT OR REPLACE INTO facts VALUES "
            "(:doc_id, :statement, :item, :label, :period, :value, :unit, :page, :chunk_id)",
            facts,
        )


def load_chunks(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc", action="append", required=True)
    parser.add_argument("--parser", default="llamaparse")
    args = parser.parse_args()

    all_facts = []
    for doc_id in args.doc:
        doc = get_doc(doc_id)
        chunks = load_chunks(chunks_path(doc, args.parser))
        for name, facts in [("income statement", extract_income_statement(chunks, doc_id)),
                            ("cash flow", extract_cash_flow(chunks, doc_id))]:
            pages = sorted({f["page"] for f in facts})
            periods = sorted({f["period"] for f in facts})
            print(f"{doc_id} {name}: {len(facts)} facts, page {pages}, periods {periods}")
            all_facts += facts

    errors = check_income_statement(all_facts) + check_cross_statement(all_facts)
    for e in errors:
        print(f"  [check] {e}")
    print("checks: OK" if not errors else f"checks: {len(errors)} failed")

    save(all_facts)
    print(f"saved to {DB_PATH}")


if __name__ == "__main__":
    main()