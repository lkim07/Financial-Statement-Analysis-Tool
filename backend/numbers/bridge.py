"""순이익 브리지(bridge, 워터폴): 순이익 변화를 항목별 기여도로 쪼갠다. EPS 브리지도 여기에 있다.

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
    (-1, "sg_a"),  # 판관비를 한 줄로 적는 회사 (Lam). AMAT는 위의 두 줄
    (-1, "restructuring"),
    (-1, "interest_expense"),
    (+1, "other_income_net"),
    (-1, "tax_provision"),
]
# 행이 없는 해(또는 그런 행이 없는 회사)에는 0. 두 해 모두 없으면 막대를 만들지 않는다.
# 빠뜨려도 안전한 이유: 아래에서 기여도의 합이 순이익 변화와 같은지 검사한다
OPTIONAL = {"restructuring", "marketing_selling", "general_admin", "sg_a", "interest_expense"}


def load_facts(doc_id: str, db_path: Path = DB_PATH) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM facts WHERE doc_id = ?", (doc_id,)).fetchall()
    return [dict(r) for r in rows]


def statement(facts: list[dict], name: str) -> list[dict]:
    """한 재무제표의 숫자만. 순이익은 손익계산서와 현금흐름표에 둘 다 있다."""
    return [f for f in facts if f.get("statement", "income_statement") == name]


def net_income_bridge(facts: list[dict], year: int | None = None) -> dict:
    """facts: 한 리포트의 숫자들. year: 올해 (기본값: 그 리포트의 가장 최근 연도)."""
    facts = statement(facts, "income_statement")
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
        if item in OPTIONAL and (item, year) not in by_key and (item, prev) not in by_key:
            continue
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


def eps_bridge(facts: list[dict], year: int | None = None) -> dict:
    """희석 EPS 변화 = 순이익 효과 + 주식 수 효과.

    순이익 효과: 주식 수를 고정하고 순이익만 바꿨을 때의 EPS 변화
    주식 수 효과: 순이익을 고정하고 주식 수만 바꿨을 때의 EPS 변화
    어느 쪽을 먼저 바꾸느냐에 따라 나뉘는 몫이 달라진다 (두 변화가 곱해지는 교차항 때문).
    그래서 두 순서의 평균을 쓴다 (Shapley 분해): 순서에 따라 결과가 바뀌지 않는다.
    """
    income = statement(facts, "income_statement")
    by_key = {(f["item"], f["period"]): f for f in income}
    cash = {(f["item"], f["period"]): f for f in statement(facts, "cash_flow")}
    year = year or max(f["period"] for f in income)
    prev = year - 1

    def get(item, period):
        if (item, period) not in by_key:
            raise KeyError(f"missing {item} {period}")
        return by_key[(item, period)]["value"]

    ni1, ni0 = get("net_income", year), get("net_income", prev)
    s1, s0 = get("shares_diluted", year), get("shares_diluted", prev)

    ni_first = (ni1 - ni0) / s0, ni1 / s1 - ni1 / s0          # 순이익 먼저, 주식 수 나중
    shares_first = ni0 / s1 - ni0 / s0, (ni1 - ni0) / s1      # 주식 수 먼저, 순이익 나중
    ni_effect = (ni_first[0] + shares_first[1]) / 2
    share_effect = (ni_first[1] + shares_first[0]) / 2
    computed = ni1 / s1 - ni0 / s0
    reported = get("eps_diluted", year) - get("eps_diluted", prev)
    # 보고된 EPS는 센트 단위로 반올림되고, 주식 수도 백만 단위로 반올림돼 있다: 0.02까지 허용
    if abs(computed - reported) > 0.02:
        raise ValueError(f"EPS bridge does not match reported EPS: computed {computed:+.3f} vs reported {reported:+.2f}")

    repurchases = cash.get(("share_repurchases", year))
    used = [by_key[(i, p)] for i in ("net_income", "shares_diluted", "eps_diluted") for p in (year, prev)]
    return {
        "doc_id": income[0]["doc_id"], "year": year, "prior_year": prev,
        "eps": get("eps_diluted", year), "eps_prior": get("eps_diluted", prev),
        "reported_change": reported, "computed_change": computed,
        "net_income": ni1, "net_income_prior": ni0, "shares": s1, "shares_prior": s0,
        "effects": {
            "net_income": ni_effect,
            "shares": share_effect,
            "net_income_first": {"net_income": ni_first[0], "shares": ni_first[1]},
            "shares_first": {"net_income": shares_first[1], "shares": shares_first[0]},
        },
        "repurchases": -repurchases["value"] if repurchases else None,  # 현금흐름표는 유출을 (음수)로 적는다
        "chunk_ids": sorted({f["chunk_id"] for f in used} | ({repurchases["chunk_id"]} if repurchases else set())),
        "pages": sorted({f["page"] for f in used} | ({repurchases["page"]} if repurchases else set())),
    }


def report_differences(facts_a: list[dict], facts_b: list[dict]) -> list[str]:
    """두 리포트에 같이 실린 (재무제표, 항목, 연도)의 값이 다르면 알려준다 = 재작성(recast) 신호."""
    b = {(f.get("statement"), f["item"], f["period"]): f["value"] for f in facts_b}
    diffs = []
    for f in facts_a:
        key = (f.get("statement"), f["item"], f["period"])
        if key in b and abs(b[key] - f["value"]) > 1.0:
            diffs.append(f"{f['item']} {f['period']}: {f['value']:,.0f} vs {b[key]:,.0f}")
    return diffs


def show(bridge: dict) -> None:
    print(f"\n{bridge['doc_id']}: net income {bridge['prior_year']} {bridge['net_income_prior']:,.0f}"
          f" -> {bridge['year']} {bridge['net_income']:,.0f}  ({bridge['change']:+,.0f})")
    # 막대 길이는 가장 큰 기여도 = 40칸 기준. 단위(백만/천 달러)와 상관없이 같은 모양이 된다
    biggest = max((abs(d["contribution"]) for d in bridge["drivers"]), default=0) or 1
    for d in bridge["drivers"]:
        bar = ("+" if d["contribution"] > 0 else "-") * round(abs(d["contribution"]) / biggest * 40)
        print(f"  {d['contribution']:+12,.0f}  {d['item']:<20} {d['prior']:>12,.0f} -> {d['current']:>12,.0f}"
              f"  p{d['page']}  {bar}")


def show_eps(b: dict) -> None:
    e = b["effects"]
    print(f"\n{b['doc_id']}: diluted EPS {b['prior_year']} {b['eps_prior']:.2f} -> {b['year']} {b['eps']:.2f}"
          f"  (reported {b['reported_change']:+.2f}, computed {b['computed_change']:+.3f})")
    print(f"  {e['net_income']:+.3f}  net income effect   {b['net_income_prior']:,.0f} -> {b['net_income']:,.0f}")
    print(f"  {e['shares']:+.3f}  share count effect  {b['shares_prior']:,.0f} -> {b['shares']:,.0f}")
    print(f"  order check: net income first {e['net_income_first']['net_income']:+.3f} / {e['net_income_first']['shares']:+.3f},"
          f" shares first {e['shares_first']['net_income']:+.3f} / {e['shares_first']['shares']:+.3f}")
    if b["repurchases"] is not None:
        print(f"  share repurchases (cash flow): {b['repurchases']:,.0f}")


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
    show_eps(eps_bridge(facts, args.year))

    if args.compare_with:
        diffs = report_differences(facts, load_facts(args.compare_with))
        print(f"\nsame item/year in {args.doc} vs {args.compare_with}: "
              + ("all equal" if not diffs else f"{len(diffs)} differ"))
        for d in diffs:
            print(f"  {d}")


if __name__ == "__main__":
    main()