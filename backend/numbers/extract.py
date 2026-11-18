"""재무제표 표 청크 -> (doc_id, statement, item, period, value) 숫자 테이블.

원칙: 숫자는 LLM이 아니라 코드가 읽고 계산한다. 여기서 뽑은 숫자로 7-2의 브리지(변동 분해)를 만든다.

사용법:
    uv run --project backend python backend/numbers/extract.py --doc amat-10k-fy2025 --doc amat-10k-fy2024

입력: data/chunks/<doc_id>/llamaparse.jsonl 의 표 청크 (LlamaParse 마크다운 표)
출력: data/numbers.db (SQLite) 의 facts 테이블
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
    """제목이 heading인 표 청크들. 표가 행 단위로 쪼개졌을 수 있으니 첫 페이지의 것을 전부 돌려준다."""
    matches = [c for c in chunks
               if c["type"] == "table" and heading.lower() in (c.get("heading") or "").lower()
               and c.get("section", "body") == "body"]
    if not matches:
        return []
    first_page = min(c["page"] for c in matches)
    return [c for c in matches if c["page"] == first_page]


def extract_income_statement(chunks: list[dict], doc_id: str) -> list[dict]:
    facts, unknown = [], set()
    for chunk in find_statement_chunks(chunks, "STATEMENTS OF OPERATIONS"):
        for row in parse_table(chunk["text"]):
            item = INCOME_STATEMENT_ITEMS.get(row["label"])
            if item is None:
                unknown.add(row["label"])
                continue
            facts.append({
                "doc_id": doc_id, "statement": "income_statement", "item": item,
                "label": row["label"], "period": row["period"], "value": row["value"],
                "unit": UNITS.get(item, "USD_millions"),
                "page": chunk["page"], "chunk_id": chunk["chunk_id"],
            })
    for label in sorted(unknown):
        print(f"  [warn] {doc_id}: unmapped label '{label}'")
    # 표가 쪼개지며 헤더 행이 반복되면 같은 숫자가 두 번 나올 수 있다: (item, period)로 하나만 남긴다
    unique = {(f["item"], f["period"]): f for f in facts}
    return list(unique.values())


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


def check_income_statement(facts: list[dict], tolerance: float = 1.0) -> list[str]:
    """검산이 틀린 곳을 문자열 목록으로. 반올림 때문에 1(백만 달러)까지는 허용한다."""
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
        facts = extract_income_statement(load_chunks(chunks_path(doc, args.parser)), doc_id)
        pages = sorted({f["page"] for f in facts})
        periods = sorted({f["period"] for f in facts})
        print(f"{doc_id}: {len(facts)} facts, page {pages}, periods {periods}")
        all_facts += facts

    errors = check_income_statement(all_facts)
    for e in errors:
        print(f"  [check] {e}")
    print("checks: OK" if not errors else f"checks: {len(errors)} failed")

    save(all_facts)
    print(f"saved to {DB_PATH}")


if __name__ == "__main__":
    main()