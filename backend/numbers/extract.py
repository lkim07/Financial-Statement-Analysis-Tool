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
    # --- 다른 회사의 표현 (Phase 9-3, Lam Research). 키는 AMAT 때 정한 것을 공통 항목으로 쓴다 ---
    "Revenue": "net_revenue",
    "Net sales": "net_revenue",
    "Total net revenue": "net_revenue",
    # 매출원가가 여러 줄이면 "Total"이 매출원가다. 나머지는 참고용 키로 따로 둔다 (함정 ②)
    "Total cost of goods sold": "cost_of_products_sold",
    "Cost of goods sold": "cost_of_goods_sold_before_restructuring",
    "Restructuring charges, net - cost of goods sold": "restructuring_in_cost_of_goods_sold",
    "Cost of revenue": "cost_of_products_sold",
    "Cost of sales": "cost_of_products_sold",
    # Lam의 "Gross margin"은 달러 금액이다 (AMAT에서는 %). 손익계산서 표의 %가 붙은 값은 따로 거른다 (함정 ③)
    "Gross margin": "gross_profit",
    "Research and development": "rd_e",
    # AMAT는 두 줄(marketing_selling, general_admin), Lam은 한 줄. 합쳐진 줄은 sg_a로 받는다
    "Selling, general, and administrative": "sg_a",
    "Selling, general and administrative": "sg_a",
    "Restructuring charges, net - operating expenses": "restructuring",
    "Total operating expenses": "total_operating_expenses",
    "Operating income": "operating_income",
    "Other income (expense), net": "other_income_net",
    "Income tax expense": "tax_provision",  # 괄호(음수)로 적는 회사가 있다: 부호는 검산으로 정한다 (함정 ④)
    "Net income per share: Basic": "eps_basic",
    "Net income per share: Diluted": "eps_diluted",
    "Number of shares used in per share calculations: Basic": "shares_basic",
    "Number of shares used in per share calculations: Diluted": "shares_diluted",
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
    # Lam Research
    "Equity-based compensation expense": "share_based_compensation",
    "Net cash provided by operating activities": "operating_cash_flow",
    "Capital expenditures and intangible assets": "capital_expenditures",
    "Net cash used for investing activities": "investing_cash_flow",
    "Treasury stock purchases": "share_repurchases",
    "Dividends paid": "dividends_paid",
    "Net cash used for financing activities": "financing_cash_flow",
}

PER_SHARE = {"eps_basic", "eps_diluted"}
RATIOS: set = set()  # %로 적힌 항목 (아직 재무제표에서 읽는 비율은 없다)
SHARES = {"shares_basic", "shares_diluted"}


def unit_of(item: str, scale: str) -> str:
    """scale: 'millions' | 'thousands' (표 위의 "(in thousands ...)"에서 읽는다, 함정 ⑤)."""
    if item in PER_SHARE:
        return "USD_per_share"
    return f"shares_{scale}" if item in SHARES else f"USD_{scale}"


SCALE = re.compile(r"\(\s*in\s+(thousands|millions|billions)", re.IGNORECASE)


def detect_scale(chunks: list[dict], default: str = "millions") -> str:
    """재무제표 청크의 제목이나 본문, 같은 쪽의 다른 청크에서 '(in thousands'를 찾는다."""
    for c in chunks:
        for text in (c.get("heading") or "", c.get("text") or ""):
            if m := SCALE.search(text):
                return m.group(1).lower()
    return default


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


DATE_YEAR = re.compile(r"^[A-Z][a-z]+\.? \d{1,2}, (20\d{2})$")  # 'June 29, 2025' (Lam 현금흐름표)


def find_years(cells: list[str]) -> list[int] | None:
    """헤더 행이면 열마다 연도를 돌려준다.

    ['Fiscal Year','2025','2024','2023'] -> [2025, 2024, 2023]
    ['', 'June 29, 2025', 'June 30, 2024', 'June 25, 2023'] -> [2025, 2024, 2023]
    """
    years = [YEAR.match(c.strip()) or DATE_YEAR.match(c.strip()) for c in cells[1:]]
    if years and all(years):
        return [int(m.group(1)) for m in years]
    return None


ANY_YEAR = re.compile(r"\b(20\d{2})\b")


def repair_header(cells: list[str]) -> tuple[list[int], list[str] | None] | None:
    """파서가 머리 행과 첫 데이터 행을 한 행으로 합친 경우 (Lam 손익계산서).

    '| June 29, …, 2025 2024 2023<br/>Revenue | Year Ended<br/>…<br/>$ 18,435,591 | … |'
    -> 연도 [2025, 2024, 2023]과 되찾은 첫 행 ['Revenue', '$ 18,435,591', …]

    칸마다 세 해가 다 적혀 있어서 "이 칸의 연도"는 알 수 없다. 대신 연도가 적힌 순서 = 값 열의 순서로 맞춘다.
    연도 수와 값 열 수가 다르면 추측하지 않는다 (None).
    """
    if not any("<br/>" in c for c in cells):
        return None
    heads = [c.rsplit("<br/>", 1)[0] if "<br/>" in c else c for c in cells]
    years = list(dict.fromkeys(int(y) for y in ANY_YEAR.findall(" ".join(heads))))  # 순서 유지, 중복 제거
    if len(years) != len(cells) - 1:
        return None
    tails = [c.rsplit("<br/>", 1)[1].strip() if "<br/>" in c else "" for c in cells]
    first_row = tails if tails[0] and any(parse_number(t) is not None for t in tails[1:]) else None
    return years, first_row


def clean_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.replace("*", "")).strip()


HEADER_ROWS = 3  # 머리 행은 여러 줄일 수 있다 ('Year Ended' 줄 + 날짜 줄). 그 이상은 머리가 아니다


def table_years(text: str) -> list[int] | None:
    """표의 머리에서 읽은 연도 (없으면 None). 쪼개진 표의 다음 청크에 넘겨줄 때 쓴다."""
    return header(text)[0]


def header(text: str) -> tuple[list[int] | None, list[list[str]]]:
    """(연도, 데이터 행들). 머리 행을 못 찾으면 연도는 None이고 모든 행이 데이터 행이다."""
    lines = [cells for cells in (split_row(ln) for ln in text.splitlines() if is_table_line(ln))
             if not is_separator(cells)]
    for i, cells in enumerate(lines[:HEADER_ROWS]):
        if years := find_years(cells):
            return years, lines[i + 1:]
        if fixed := repair_header(cells):  # 머리 행과 첫 데이터 행이 합쳐진 경우
            years, first = fixed
            return years, ([first] if first else []) + lines[i + 1:]
    return None, lines


def parse_table(text: str, fallback_years: list[int] | None = None) -> list[dict]:
    """마크다운 표 하나 -> [{"label", "period", "value", "percent"}, ...]

    - 머리 행(처음 몇 줄)에서 연도 열을 찾는다. 못 찾으면 fallback_years(같은 표의 앞 청크)를 쓰고,
      그것도 없으면 빈 목록 (연도 표가 아니다).
    - 값이 하나도 없는 행("Operating expenses:")은 섹션 라벨: 아래 행 라벨 앞에 붙인다.
    """
    years, data = header(text)
    if years is None:
        if not fallback_years:
            return []
        years = fallback_years
    section = ""
    rows = []
    for cells in data:
        label, values = clean_label(cells[0]), cells[1:]
        if not label:
            continue
        numbers = [parse_number(v) for v in values]
        if all(n is None for n in numbers):  # 섹션 라벨 행
            section = label.rstrip(":") + ":"
            continue
        full_label = f"{section} {label}" if section else label
        for period, value, raw in zip(years, numbers, values):
            if value is not None:
                rows.append({"label": full_label, "period": period, "value": value, "percent": "%" in raw})
        if label in SECTION_ENDS:
            section = ""
    return rows


MIN_ITEMS = 4  # 표 하나에서 이만큼의 항목이 매핑되어야 재무제표 후보로 본다


def find_by_content(chunks: list[dict], items: dict[str, str]) -> list[dict]:
    """제목으로 못 찾았을 때: 라벨이 가장 많이 매핑되는 쪽의 표들 (Lam은 제목 자리를 단위 줄이 차지했다).

    주석의 EPS 표나 분기 실적 표도 'Net income'과 'per share'를 갖고 있다. 그래서 표 하나가 아니라
    쪽(page) 단위로 서로 다른 항목 수를 세고, 가장 많은 쪽을 고른다. 진짜 손익계산서는 매출부터
    주식 수까지 전부 있다. 마지막 판정은 검산이 한다.
    """
    by_page: dict[int, set] = {}
    tables: dict[int, list] = {}
    for c in chunks:
        if c["type"] != "table" or c.get("section", "body") != "body":
            continue
        rows = parse_table(c["text"])
        found = {items[r["label"]] for r in rows if r["label"] in items}
        found |= {items[r["label"].split(": ")[-1]] for r in rows if r["label"].split(": ")[-1] in items}
        if found:
            by_page.setdefault(c["page"], set()).update(found)
            tables.setdefault(c["page"], []).append(c)
    candidates = {p: f for p, f in by_page.items() if len(f) >= MIN_ITEMS}
    if not candidates:
        return []
    best = max(candidates, key=lambda p: (len(candidates[p]), -p))  # 항목이 같으면 앞쪽
    run = [best]
    # 다음 쪽으로 이어지는 표 (Lam 현금흐름표의 재무활동 부분): 이어지는 쪽은 한 항목만 맞아도 된다
    while run[-1] + 1 in tables:
        run.append(run[-1] + 1)
    # 그 쪽들의 표는 전부: 쪼개진 표의 뒤 청크는 머리 행(연도)이 없어서 위에서 세지 못했다
    return [c for c in chunks if c["page"] in run and c["type"] == "table" and c.get("section", "body") == "body"]


def find_statement_chunks(chunks: list[dict], heading: str, items: dict[str, str] | None = None) -> list[dict]:
    """제목이 heading인 표 청크들.

    표가 행 단위로 쪼개졌을 수 있고, 다음 페이지로 이어질 수도 있다 ("— (Continued)").
    첫 페이지부터 페이지가 끊기지 않고 이어지는 동안의 청크를 전부 돌려준다.
    (재무제표 본표만 잡고, 한참 뒤 주석에 같은 제목이 나와도 섞이지 않게)
    """
    matches = [c for c in chunks
               if c["type"] == "table" and heading.lower() in (c.get("heading") or "").lower()
               and c.get("section", "body") == "body"]
    if not matches and items:
        matches = find_by_content(chunks, items)
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
    found = find_statement_chunks(chunks, heading, items)
    scale = detect_scale(found + [c for c in chunks if c["page"] in {f["page"] for f in found}])
    years = None
    for chunk in found:  # 청크 순서 = 표의 순서. 쪼개진 뒤 청크는 앞 청크의 연도를 이어받는다
        years = table_years(chunk["text"]) or years
        for row in parse_table(chunk["text"], fallback_years=years):
            label = row["label"].split(": ")[-1] if by_last_label else row["label"]
            item = items.get(label)
            if item is None:
                unknown.add(row["label"])
                continue
            if row["percent"] and item not in RATIOS:  # 금액 항목에 %가 붙어 있으면 다른 표다 (함정 ③)
                unknown.add(f"{row['label']} (percent)")
                continue
            facts.append({
                "doc_id": doc_id, "statement": statement, "item": item,
                "label": row["label"], "period": row["period"], "value": row["value"],
                "unit": unit_of(item, scale),
                "page": chunk["page"], "chunk_id": chunk["chunk_id"],
            })
    if warn:
        for label in sorted(unknown):
            print(f"  [warn] {doc_id}: unmapped label '{label}'")
    # 표가 쪼개지며 헤더 행이 반복되면 같은 숫자가 두 번 나올 수 있다: (item, period)로 하나만 남긴다
    unique = {(f["item"], f["period"]): f for f in facts}
    return list(unique.values())


def extract_income_statement(chunks: list[dict], doc_id: str) -> list[dict]:
    facts = extract_statement(chunks, doc_id, "income_statement", "STATEMENTS OF OPERATIONS", INCOME_STATEMENT_ITEMS)
    return normalize_tax_sign(facts)


def normalize_tax_sign(facts: list[dict], tolerance: float = 1.0) -> list[dict]:
    """세금은 '비용 = 양수'로 저장한다 (AMAT 방식). 괄호(음수)로 적은 회사는 검산으로 알아낸다.

    세전이익 - 세금 = 순이익이면 그대로, 세전이익 + 세금 = 순이익이면 부호를 뒤집는다.
    라벨 이름으로 추측하지 않는다: 'Provision for (benefit from) income taxes'처럼 정말 음수(환급)인 해도 있다.
    """
    v = {(f["item"], f["period"]): f for f in facts}
    for (item, period), f in v.items():
        if item != "tax_provision":
            continue
        pre, ni = v.get(("pretax_income", period)), v.get(("net_income", period))
        if not pre or not ni:
            continue
        if abs(pre["value"] - f["value"] - ni["value"]) > tolerance \
                and abs(pre["value"] + f["value"] - ni["value"]) <= tolerance:
            f["value"] = -f["value"] + 0.0
    return facts


def extract_cash_flow(chunks: list[dict], doc_id: str) -> list[dict]:
    return extract_statement(chunks, doc_id, "cash_flow", "STATEMENTS OF CASH FLOWS", CASH_FLOW_ITEMS,
                             by_last_label=True, warn=False)


# --- 검산 (합계 = 항목들의 합) ----------------------------------------------

# (결과 항목, [(부호, 항목), ...]): 결과 = 부호를 붙인 항목들의 합
INCOME_STATEMENT_CHECKS = [
    ("gross_profit", [(1, "net_revenue"), (-1, "cost_of_products_sold")]),
    ("cost_of_products_sold", [(1, "cost_of_goods_sold_before_restructuring"),
                              (1, "restructuring_in_cost_of_goods_sold")]),
    ("total_operating_expenses", [(1, "rd_e"), (1, "marketing_selling"), (1, "general_admin"),
                                  (1, "sg_a"), (1, "restructuring")]),
    ("operating_income", [(1, "gross_profit"), (-1, "total_operating_expenses")]),
    ("pretax_income", [(1, "operating_income"), (-1, "interest_expense"), (1, "other_income_net")]),
    ("net_income", [(1, "pretax_income"), (-1, "tax_provision")]),
]
# 없는 해(또는 그 회사)에는 행 자체가 없을 수 있다 -> 0으로 본다.
# 0으로 봐도 안전한 이유: 합계를 함께 검사하니까, 있어야 할 행이 빠지면 합계가 어긋나서 잡힌다
OPTIONAL = {"restructuring", "marketing_selling", "general_admin", "sg_a", "interest_expense",
            "restructuring_in_cost_of_goods_sold"}
# 이 검산은 구성 항목이 하나도 없으면 건너뛴다 (AMAT는 매출원가를 한 줄로만 적는다)
SKIP_IF_NO_PARTS = {"cost_of_products_sold"}


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
            if total in SKIP_IF_NO_PARTS and not any((doc_id, period, i) in values for _, i in parts):
                continue
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


def replace_facts(doc_id: str, facts: list[dict], db_path: Path = DB_PATH) -> None:
    """업로드 파이프라인용: 이 문서의 숫자를 지우고 다시 넣는다 (INSERT OR REPLACE만으로는 옛 행이 남는다)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(SCHEMA)
        conn.execute("DELETE FROM facts WHERE doc_id = ?", (doc_id,))
    if facts:
        save(facts, db_path)


# 이 항목이 없으면 '조용한 누락'이다: 검산은 있는 숫자끼리만 맞춰 보므로, 통째로 빠진 표는 못 잡는다
REQUIRED = {
    "income_statement": ["net_revenue", "operating_income", "pretax_income", "tax_provision", "net_income",
                         "eps_diluted", "shares_diluted"],
    "cash_flow": ["net_income", "operating_cash_flow", "capital_expenditures", "share_repurchases"],
}


def check_coverage(facts: list[dict]) -> list[str]:
    """꼭 있어야 할 항목이 가장 최근 두 해에 있는가 (브리지와 대시보드가 두 해를 비교한다)."""
    gaps = []
    for statement, items in REQUIRED.items():
        mine = [f for f in facts if f["statement"] == statement]
        if not mine:
            gaps.append(f"{statement.replace('_', ' ')} not found")
            continue
        years = sorted({f["period"] for f in mine})[-2:]
        have = {(f["item"], f["period"]) for f in mine}
        missing = [f"{i} {y}" for i in items for y in years if (i, y) not in have]
        if missing:
            gaps.append(f"{statement.replace('_', ' ')} missing {', '.join(missing)}")
    return gaps


def extract_document(doc_id: str, chunks: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """손익계산서와 현금흐름표를 뽑고 검사한다. (숫자들, 검산 오류, 빠진 항목).

    검산 오류 = 숫자가 틀렸다 -> 저장하지 않는다. 빠진 항목 = 있는 숫자는 맞다 -> 저장하고 경고한다.
    """
    facts = extract_income_statement(chunks, doc_id) + extract_cash_flow(chunks, doc_id)
    gaps = check_coverage(facts)
    if not any(f["statement"] == "income_statement" for f in facts):
        return facts, ["income statement not found"], gaps
    return facts, check_income_statement(facts) + check_cross_statement(facts), gaps


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
    for doc_id in args.doc:
        gaps = check_coverage([f for f in all_facts if f["doc_id"] == doc_id])
        print(f"coverage {doc_id}: " + ("OK" if not gaps else "; ".join(gaps)))

    save(all_facts)
    print(f"saved to {DB_PATH}")


if __name__ == "__main__":
    main()