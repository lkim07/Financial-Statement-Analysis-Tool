"""질문 분해(query decomposition): "순이익이 왜 변했나"를 원인 항목별 검색어로 바꾼다.

흐름:
    질문 -> 순이익 변화 질문인가? -> 어느 리포트, 어느 해의 브리지인가?
         -> 누적 기여도 coverage(기본 80%)까지의 원인 항목 (최소 1개, 최대 4개)
         -> 항목마다 리포트 문장처럼 쓴 검색어 (HyDE 방식)
    검색 결과는 merge_seats로 합쳐서 정해진 개수(budget)만 LLM에 넘긴다.

숫자 근거(손익계산서 행)는 검색하지 않는다: 브리지가 쓴 숫자의 chunk_id를 이미 알고 있다
(decision 022). 그 청크를 고정석(fixed seat)으로 넣는다. 숫자는 코드가, 문장은 검색이.

검색어 템플릿에는 어느 10-K에나 나오는 회계 용어만 쓴다.
이 리포트에만 나오는 단어(특정 금액, 법 이름)를 넣으면 평가셋에 과적합(overfitting)된다.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "numbers"))
from bridge import eps_bridge, load_facts, net_income_bridge  # noqa: E402

# {dir}: 그 항목이 늘었으면 "increased", 줄었으면 "decreased"
DRIVER_QUERIES = {
    "tax_provision": "The effective tax rate {dir} compared to the prior year primarily due to changes in "
                     "tax credits, deferred tax assets and valuation allowances",
    "gross_profit": "Gross margin {dir} primarily due to changes in average selling prices, product mix, "
                    "and material and manufacturing costs",
    "other_income_net": "Interest and other income, net {dir} primarily due to gains or losses on investments "
                        "and interest income",
    "rd_e": "Research, development and engineering expenses {dir} primarily due to headcount and "
            "investments in product development",
    "general_admin": "General and administrative expenses {dir} primarily due to employee-related costs",
    "marketing_selling": "Marketing and selling expenses {dir} primarily due to employee-related costs",
    "restructuring": "Restructuring charges related to workforce reduction and severance",
    "interest_expense": "Interest expense {dir} due to changes in outstanding debt and senior notes",
    # EPS 브리지의 주식 수 효과
    "shares_diluted": "Weighted average shares outstanding {dir} primarily due to repurchases of common stock "
                      "under the stock repurchase program",
}

# 순이익 변화를 묻는가: "net income", "net earnings", "profit" + 변화/원인 단어.
# EPS("earnings per share") 질문은 주식 수가 얽혀서 이 브리지로 풀리지 않는다 -> 제외
PROFIT = re.compile(r"\b(net income|net earnings|net profit|profit)\b", re.IGNORECASE)
CHANGE = re.compile(r"\b(why|increase[ds]?|decrease[ds]?|decline[ds]?|change[ds]?|drop(ped)?|grow|grew)\b",
                    re.IGNORECASE)
EPS = re.compile(r"\b(eps|per share)\b", re.IGNORECASE)
# 분해는 "왜"를 묻는 질문에만: "얼마나 변했나"(숫자 질문)에 붙이면 고정석과 원인 검색어가
# 원래 질문의 자리를 빼앗는다 (q50: 1.00 -> 0.00). 질문 유형 라우터(7-5)가 나중에 이 역할을 맡는다.
WHY = re.compile(r"\b(why|reasons?|explain\w*|drove|drivers?|driven|caused?|causes)\b", re.IGNORECASE)
YEAR = re.compile(r"\b(?:fy|fiscal(?:\s+year)?)\s?(20\d{2})\b", re.IGNORECASE)


def is_profit_change_question(question: str) -> bool:
    return bool(WHY.search(question) and PROFIT.search(question) and CHANGE.search(question)
                and not EPS.search(question))


def is_eps_change_question(question: str) -> bool:
    return bool(WHY.search(question) and EPS.search(question) and CHANGE.search(question))


def bridge_source(question: str, registry: list[dict]) -> tuple[str, int] | None:
    """(브리지를 계산할 리포트, 연도). 규칙: Y년 변화는 Y년 리포트에서 (같은 잣대).

    연도가 없으면 최신 리포트의 최신 연도. Y년 리포트가 없으면 Y+1년 리포트의 비교 연도를 쓴다.
    """
    by_year = {d["fiscal_year"]: d["doc_id"] for d in registry}
    years = [int(y) for y in YEAR.findall(question)]
    year = max(years) if years else max(by_year)
    if year in by_year:
        return by_year[year], year
    if year + 1 in by_year:
        return by_year[year + 1], year
    return None


def select_drivers(bridge: dict, coverage: float = 0.8, min_drivers: int = 1, max_drivers: int = 4) -> list[dict]:
    """|기여도|가 큰 순서로, 누적 비율이 coverage에 닿을 때까지.

    분모는 순이익 변화가 아니라 |기여도|의 합: FY2025처럼 순변화(-179)가 작고
    항목들이 서로 상쇄하는(+911, -1,298) 해에는 순변화로 나누면 비율이 의미가 없다.
    """
    drivers = [d for d in bridge["drivers"] if d["contribution"] != 0]
    total = sum(abs(d["contribution"]) for d in drivers)
    chosen, covered = [], 0.0
    for d in drivers:  # 이미 |기여도| 순으로 정렬돼 있다
        if len(chosen) >= max_drivers:
            break
        if len(chosen) >= min_drivers and covered / total >= coverage:
            break
        chosen.append(d)
        covered += abs(d["contribution"])
    return chosen


def driver_query(driver: dict) -> str:
    direction = "increased" if driver["current"] > driver["prior"] else "decreased"
    return DRIVER_QUERIES[driver["item"]].format(dir=direction)


def plan(question: str, registry: list[dict], coverage: float = 0.8, facts_loader=load_facts) -> dict | None:
    """이 질문에 대한 분해 계획. 해당 없는 질문이면 None.

    {"kind": "net_income" | "eps", "bridge_doc", "year",
     "drivers": [{"item", "contribution", "query"}...],
     "statement_chunks": [브리지 숫자가 나온 청크 id들 = 고정석]}
    """
    if is_eps_change_question(question):
        return eps_plan(question, registry, coverage, facts_loader)
    if not is_profit_change_question(question):
        return None
    source = bridge_source(question, registry)
    if source is None:
        return None
    doc_id, year = source
    facts = facts_loader(doc_id)
    if not facts:
        return None
    bridge = net_income_bridge(facts, year)
    drivers = select_drivers(bridge, coverage)
    statement_chunks = sorted({f["chunk_id"] for f in facts
                               if f.get("statement", "income_statement") == "income_statement"
                               and f["period"] in (year, year - 1)
                               and f["item"] in {"net_income", *(d["item"] for d in drivers)}})
    return {
        "kind": "net_income",
        "bridge_doc": doc_id,
        "year": year,
        "drivers": [{"item": d["item"], "contribution": d["contribution"], "query": driver_query(d)}
                    for d in drivers],
        "statement_chunks": statement_chunks,
    }


def eps_plan(question: str, registry: list[dict], coverage: float, facts_loader) -> dict | None:
    """EPS = 순이익 / 주식 수. 원인은 두 갈래다.

    - 주식 수 효과: 자사주 매입 (현금흐름표, 주식 수 행)
    - 순이익 효과: 순이익 브리지의 가장 큰 원인 1개 (FY2025라면 세금)
    고정석: 손익계산서(EPS, 주식 수, 순이익 행) + 현금흐름표(자사주 매입 행)
    """
    source = bridge_source(question, registry)
    if source is None:
        return None
    doc_id, year = source
    facts = facts_loader(doc_id)
    if not facts:
        return None
    eb = eps_bridge(facts, year)
    shares = {"item": "shares_diluted", "current": eb["shares"], "prior": eb["shares_prior"],
              "contribution": eb["effects"]["shares"]}
    ni_top = select_drivers(net_income_bridge(facts, year), coverage, max_drivers=1)
    drivers = [shares, *ni_top]
    return {
        "kind": "eps",
        "bridge_doc": doc_id,
        "year": year,
        "drivers": [{"item": d["item"], "contribution": d["contribution"], "query": driver_query(d)}
                    for d in drivers],
        "statement_chunks": eb["chunk_ids"],
    }


def merge_seats(original: list, per_driver: list[list], budget: int, key=lambda x: x,
                fixed: list | None = None, keep_original: int = 4) -> list:
    """검색 결과 목록들을 합쳐 budget개만 남긴다. 중복은 key로 제거.

    자리 배정 순서:
    1) 고정석: 브리지 숫자가 나온 청크 (숫자 근거는 검색 없이 확정)
    2) 원래 질문의 상위 keep_original개 (분해가 원래 검색을 밀어내지 않게)
    3) 원인마다 1등 청크 (작은 원인도 최소 한 자리)
    4) 남는 자리는 [원래 질문, 원인1, 원인2, ...]를 돌아가며 하나씩
    """
    merged, seen = [], set()

    def take(item) -> bool:
        k = key(item)
        if k in seen:
            return False
        seen.add(k)
        merged.append(item)
        return True

    for h in fixed or []:
        if len(merged) >= budget:
            return merged
        take(h)

    for h in original[:keep_original]:
        if len(merged) >= budget:
            return merged
        take(h)

    for hits in per_driver:
        for h in hits:
            if len(merged) >= budget:
                return merged
            if take(h):
                break

    queues = [list(original), *[list(h) for h in per_driver]]
    while len(merged) < budget and any(queues):
        for q in queues:
            while q:
                if take(q.pop(0)):
                    break
            if len(merged) >= budget:
                break
    return merged