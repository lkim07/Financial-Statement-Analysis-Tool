"""리포트 요약: 핵심 지표와 순이익 워터폴 (Phase 8-4).

숫자는 전부 숫자 테이블(decision 022)과 브리지(decision 023)에서 온다. LLM은 쓰지 않는다.
여기 함수는 순수하다 (Milvus, PDF 없이 테스트). 하이라이트는 service.report_summary()가 붙인다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "numbers"))
from bridge import net_income_bridge, statement  # noqa: E402

KPIS = [  # (항목, 화면 이름, 종류)
    ("net_revenue", "Net revenue", "usd"),
    ("gross_margin", "Gross margin", "pct"),
    ("operating_income", "Operating income", "usd"),
    ("net_income", "Net income", "usd"),
    ("eps_diluted", "Diluted EPS", "per_share"),
]


def pdf_text(value: float, unit: str) -> str:
    """PDF에 적힌 모양 그대로: 2,273 / 8.66. 음수는 괄호로 적혀 있으니 절댓값으로 찾는다."""
    return f"{abs(value):.2f}" if unit == "USD_per_share" else f"{abs(value):,.0f}"


def kpis(facts: list[dict], year: int) -> list[dict]:
    income = {(f["item"], f["period"]): f for f in statement(facts, "income_statement")}
    out = []
    for item, name, kind in KPIS:
        if item == "gross_margin":  # 손익계산서에 없는 비율: 코드가 계산한다
            cur = income[("gross_profit", year)]["value"] / income[("net_revenue", year)]["value"] * 100
            prev = income[("gross_profit", year - 1)]["value"] / income[("net_revenue", year - 1)]["value"] * 100
            out.append({"item": item, "name": name, "kind": kind, "current": round(cur, 1),
                        "prior": round(prev, 1), "change": round(cur - prev, 1), "computed": True,
                        "facts": [income[("gross_profit", year)], income[("net_revenue", year)]]})
            continue
        cur, prev = income[(item, year)], income[(item, year - 1)]
        change = cur["value"] - prev["value"]
        out.append({"item": item, "name": name, "kind": kind, "current": cur["value"], "prior": prev["value"],
                    "change": round(change, 2),
                    "change_pct": round(change / abs(prev["value"]) * 100, 1) if prev["value"] else None,
                    "computed": False, "facts": [cur]})
    return out


def waterfall(facts: list[dict], year: int) -> dict:
    """막대마다 시작과 끝 높이(누적)를 계산한다. 화면은 그대로 그리기만 한다."""
    b = net_income_bridge(facts, year)
    income = {(f["item"], f["period"]): f for f in statement(facts, "income_statement")}
    bars = [{"item": "net_income", "label": f"FY{b['prior_year']} net income", "kind": "total",
             "start": 0, "end": b["net_income_prior"], "value": b["net_income_prior"],
             "fact": income[("net_income", b["prior_year"])]}]
    running = b["net_income_prior"]
    # 막대 순서는 기여도 크기 순 (브리지의 정렬 그대로): 가장 큰 원인이 왼쪽에 온다
    for d in b["drivers"]:
        if d["contribution"] == 0:
            continue
        fact = income.get((d["item"], year)) or income.get((d["item"], year - 1))
        if d["current"] == 0 and (d["item"], year - 1) in income:  # 올해가 —(0)이면 작년 값으로 행을 찾는다
            fact = income[(d["item"], year - 1)]
        bars.append({"item": d["item"], "label": d["label"].split(": ")[-1], "kind": "up" if d["contribution"] > 0 else "down",
                     "start": running, "end": running + d["contribution"], "value": d["contribution"],
                     "current": d["current"], "prior": d["prior"], "fact": fact,
                     "question": why_question(d, year)})
        running += d["contribution"]
    bars.append({"item": "net_income", "label": f"FY{year} net income", "kind": "total",
                 "start": 0, "end": b["net_income"], "value": b["net_income"],
                 "fact": income[("net_income", year)]})
    return {"year": year, "prior_year": b["prior_year"], "change": b["change"], "bars": bars}


def why_question(driver: dict, year: int) -> str:
    """막대 옆 "Why?" 버튼이 Ask 탭에 넘길 질문. 브리지 분해가 아니라 그 항목의 원인을 묻는다."""
    label = driver["label"].split(": ")[-1]
    direction = "increase" if driver["current"] > driver["prior"] else "decrease"
    return f"Why did {label[0].lower() + label[1:]} {direction} in fiscal {year}?"