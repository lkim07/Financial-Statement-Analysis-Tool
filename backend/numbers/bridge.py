"""순이익 브리지(bridge, 워터폴): 순이익 변화를 항목별 기여도로 쪼갠다.

    순이익 변화 = Σ 부호 × (올해 값 − 작년 값)
    비용 항목은 부호가 −: 비용이 늘면 순이익이 준다.

규칙: 두 연도 값은 모두 같은 리포트(Y년 리포트)에서 가져온다. 같은 잣대(재작성 기준)로 비교하기 위해서.

사용법:
    uv run --project backend python backend/numbers/bridge.py --doc amat-10k-fy2025
    uv run --project backend python backend/numbers/bridge.py --doc amat-10k-fy2024
"""

import argparse
import sqlite3
from pathlib import Path

DB_PATH = Path("data/numbers.db")

# (부호, 항목): 이 항목들의 기여도를 더하면 순이익 변화와 정확히 같아야 한다
NET_INCOME_BRIDGE = [
    (+1, "gross_profit"),
    (-1, "rd_e"),
    (-1, "marketing_selling"),
    (-1, "general_admin"),
    (-1, "restructuring"),
    (-1, "interest_expense"),
    (+1, "other_income_net"),
    (-1, "tax_provision"),
]
OPTIONAL = {"restructuring"}  # 행이 없는 해에는 0


def load_facts(doc_id: str, db_path: Path = DB_PATH) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM facts WHERE doc_id = ?", (doc_id,)).fetchall()
    return [dict(r) for r in rows]


def net_income_bridge(facts: list[dict], year: int | None = None) -> dict:
    """facts: 한 리포트의 숫자들. year: 올해 (기본값: 그 리포트의 가장 최근 연도)."""
    by_key = {(f["item"], f["period"]): f for f in facts}
    year = year or max(f["period"] for f in facts)
    prev = year - 1

    def value(item, period):
        if (item, period) in by_key:
            return by_key[(item, period)]["value"]
        if item in OPTIONAL:
            return 0.0
        raise KeyError(f"missing {item} {period}")

    drivers = []
    for sign, item in NET_INCOME_BRIDGE:
        current, before = value(item, year), value(item, prev)
        fact = by_key.get((item, year)) or by_key.get((item, prev))
        drivers.append({
            "item": item,
            "label": fact["label"] if fact else item,
            "current": current,
            "prior": before,
            "contribution": sign * (current - before) + 0.0,  # + 0.0: -0.0을 0.0으로
            "page": fact["page"] if fact else None,
            "chunk_id": fact["chunk_id"] if fact else None,
        })

    ni_now, ni_before = value("net_income", year), value("net_income", prev)
    total = sum(d["contribution"] for d in drivers)
    change = ni_now - ni_before
    if abs(total - change) > 1.0:  # 반올림 허용 1
        raise ValueError(f"bridge does not add up: drivers {total:,.0f} vs net income change {change:,.0f}")

    return {
        "doc_id": facts[0]["doc_id"],
        "year": year,
        "prior_year": prev,
        "net_income": ni_now,
        "net_income_prior": ni_before,
        "change": change,
        # 기여도 크기 순: 맨 앞이 가장 큰 원인 (부호와 상관없이)
        "drivers": sorted(drivers, key=lambda d: abs(d["contribution"]), reverse=True),
    }


def report_differences(facts_a: list[dict], facts_b: list[dict]) -> list[str]:
    """두 리포트에 같이 실린 (항목, 연도)의 값이 다르면 알려준다 = 재작성(recast) 신호."""
    b = {(f["item"], f["period"]): f["value"] for f in facts_b}
    diffs = []
    for f in facts_a:
        key = (f["item"], f["period"])
        if key in b and abs(b[key] - f["value"]) > 1.0:
            diffs.append(f"{f['item']} {f['period']}: {f['value']:,.0f} vs {b[key]:,.0f}")
    return diffs


def show(bridge: dict) -> None:
    print(f"\n{bridge['doc_id']}: net income {bridge['prior_year']} {bridge['net_income_prior']:,.0f}"
          f" -> {bridge['year']} {bridge['net_income']:,.0f}  ({bridge['change']:+,.0f})")
    for d in bridge["drivers"]:
        bar = "+" * round(abs(d["contribution"]) / 100) if d["contribution"] > 0 else "-" * round(abs(d["contribution"]) / 100)
        print(f"  {d['contribution']:+8,.0f}  {d['item']:<20} {d['prior']:>9,.0f} -> {d['current']:>9,.0f}"
              f"  p{d['page']}  {bar}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc", required=True)
    parser.add_argument("--year", type=int)
    parser.add_argument("--compare-with", help="another doc_id: report values that differ between the two reports")
    args = parser.parse_args()

    facts = load_facts(args.doc)
    if not facts:
        raise SystemExit(f"no facts for {args.doc}. run backend/numbers/extract.py first")
    show(net_income_bridge(facts, args.year))

    if args.compare_with:
        diffs = report_differences(facts, load_facts(args.compare_with))
        print(f"\nsame item/year in {args.doc} vs {args.compare_with}: "
              + ("all equal" if not diffs else f"{len(diffs)} differ"))
        for d in diffs:
            print(f"  {d}")


if __name__ == "__main__":
    main()