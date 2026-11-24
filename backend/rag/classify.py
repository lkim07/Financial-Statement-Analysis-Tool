"""질문 분류: 두 축 (decision 026).

    문서 종류 (doc_types):    10-K / DEF 14A (프록시) / 둘 다
    질문 유형 (question_type): numeric (얼마) / why (왜) / compare (비교)

세 가지 방식:
- rule:    정규식. 공짜, 결정적, 어느 단어에 걸렸는지 설명할 수 있다.
- llm:     작은 LLM 호출 + 구조화 출력. 처음 보는 표현도 의미로 판단한다. 비용이 든다.
- cascade: 규칙이 확신할 때는 규칙, 확신하지 못할 때만 LLM.
           확신하지 못함 = 문서 종류 단어가 하나도 안 걸림 (기본값 추측)
                         또는 프록시 단어와 재무 단어가 둘 다 걸림 (두 문서를 다 여는 비싼 경로)
"""

import re
from typing import Literal

from pydantic import BaseModel, Field
from router import FINANCIAL_WORDS, PROXY, PROXY_WORDS, TEN_K

WHY_Q = re.compile(r"\b(why|reasons?|explain\w*|drove|drivers?|driven|caused?|causes|what led)\b", re.IGNORECASE)
COMPARE_Q = re.compile(
    r"\b(compare[ds]?|comparison|versus|vs\.?|differ\w*|than|relative to|between|trend|mix|"
    r"same direction|faster|slower|largest|most|change[ds]?)\b",
    re.IGNORECASE,
)


def rule_classify(question: str) -> dict:
    proxy = PROXY_WORDS.findall(question)
    financial = FINANCIAL_WORDS.findall(question)
    if proxy and not financial:
        doc_types, confident = [PROXY], True
    elif financial and not proxy:
        doc_types, confident = [TEN_K], True
    elif proxy and financial:
        doc_types, confident = [TEN_K, PROXY], False  # 둘 다: 비싼 경로라 LLM에게 줄일 기회를 준다
    else:
        doc_types, confident = [TEN_K], False  # 아무것도 안 걸림: 기본값 추측

    if WHY_Q.search(question):
        question_type = "why"
    elif COMPARE_Q.search(question):
        question_type = "compare"
    else:
        question_type = "numeric"

    words = lambda found: ", ".join(sorted({w.lower() for w in found})) or "-"  # noqa: E731
    return {"doc_types": doc_types, "question_type": question_type, "confident": confident,
            "reason": f"proxy words: {words(proxy)}; financial words: {words(financial)}"}


class Route(BaseModel):
    doc_types: list[Literal["10-K", "DEF 14A"]] = Field(
        description="Documents needed to answer. Use both only if the question needs facts from both.")
    question_type: Literal["numeric", "why", "compare"]
    reason: str = Field(description="One short sentence.")


ROUTER_INSTRUCTIONS = """You route questions about one company's SEC filings.

Documents:
- 10-K (annual report): business, risk factors, MD&A, audited financial statements and notes
  (revenue, margins, net income, EPS, taxes, cash flow, segments, regions, share repurchases, employees).
- DEF 14A (proxy statement): annual shareholder meeting, board and director nominees, director independence,
  executive compensation (CEO pay, pay ratio, pay versus performance), say-on-pay votes, auditor fees and
  ratification, stock ownership, shareholder proposals.
  It repeats a few financial figures (e.g. net income in the pay-versus-performance table) but the 10-K is
  the source for financial results.

Question types:
- numeric: asks for a value or a count ("what was", "how much", "how many").
- why: asks for causes or reasons.
- compare: asks how values differ across periods, segments, regions or items.

Return only the documents the question needs."""


def llm_classify(question: str, client, model: str) -> tuple[dict, dict]:
    """(분류, 사용량). 사용량은 비용 비교용 {"input_tokens", "output_tokens"}."""
    response = client.responses.parse(model=model, instructions=ROUTER_INSTRUCTIONS, input=question,
                                      text_format=Route)
    r = response.output_parsed
    usage = getattr(response, "usage", None)
    tokens = {"input_tokens": getattr(usage, "input_tokens", 0) or 0,
              "output_tokens": getattr(usage, "output_tokens", 0) or 0}
    order = {TEN_K: 0, PROXY: 1}
    return ({"doc_types": sorted(set(r.doc_types), key=order.get), "question_type": r.question_type,
             "confident": True, "reason": r.reason}, tokens)


def classify(question: str, mode: str = "rule", client=None, model: str | None = None) -> tuple[dict, dict | None]:
    """mode: rule | llm | cascade. 돌려주는 값: (분류, LLM 사용량 또는 None)"""
    if mode == "rule":
        return rule_classify(question), None
    if mode == "llm":
        return llm_classify(question, client, model)
    rule = rule_classify(question)
    if rule["confident"]:
        return rule, None
    result, tokens = llm_classify(question, client, model)
    result["reason"] = f"rule unsure ({rule['reason']}) -> llm: {result['reason']}"
    return result, tokens


def route_question(question: str, registry: list[dict], route: str, client=None, model: str | None = None) -> list[str] | None:
    """질문 -> 검색할 doc_id 목록. route: none | rule | cascade | llm.

    rule은 router.route_docs의 규칙 그대로. cascade/llm은 문서 종류만 분류기가 정하고,
    연도 고르기는 규칙(router.route_within_type)이 한다: 연도는 정규식으로 충분히 정확하다.
    """
    from router import route_docs

    if route == "none":
        return None
    if route == "rule":
        return route_docs(question, registry)
    result, _ = classify(question, route, client, model)
    return route_docs(question, registry, result["doc_types"])