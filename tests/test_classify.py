"""질문 분류 테스트: 규칙, 확신 여부, 계단식 (LLM은 가짜)."""
from types import SimpleNamespace as NS

from classify import Route, classify, rule_classify


def test_rule_is_confident_when_one_kind_of_word_matches():
    r = rule_classify("What was the CEO's total compensation for fiscal 2025?")
    assert r["doc_types"] == ["DEF 14A"] and r["confident"] and "compensation" in r["reason"]
    r = rule_classify("Why did gross margin increase in FY2025?")
    assert r["doc_types"] == ["10-K"] and r["confident"] and r["question_type"] == "why"


def test_rule_is_unsure_when_nothing_matches():
    r = rule_classify("What were the total fees paid to KPMG for fiscal 2025?")  # q54
    assert r["doc_types"] == ["10-K"] and not r["confident"]


def test_rule_is_unsure_when_both_kinds_match():
    r = rule_classify("In fiscal 2025, did the CEO's total compensation move in the same direction as net income?")
    assert r["doc_types"] == ["10-K", "DEF 14A"] and not r["confident"]
    assert r["question_type"] == "compare"


def test_question_type_rules():
    assert rule_classify("How did revenue growth in Korea compare with Japan?")["question_type"] == "compare"
    assert rule_classify("What were total inventories at the end of FY2025?")["question_type"] == "numeric"


class FakeLLM:
    """responses.parse만 흉내 낸다. 몇 번 불렸는지 센다."""
    def __init__(self, route):
        self.calls = 0
        self.responses = self
        self.route = route

    def parse(self, model, instructions, input, text_format):
        self.calls += 1
        return NS(output_parsed=self.route, usage=NS(input_tokens=300, output_tokens=30))


def test_cascade_calls_llm_only_when_rule_is_unsure():
    llm = FakeLLM(Route(doc_types=["DEF 14A"], question_type="numeric", reason="auditor fees are in the proxy"))
    sure, tokens = classify("Why did gross margin increase?", "cascade", llm, "m")
    assert llm.calls == 0 and tokens is None and sure["doc_types"] == ["10-K"]
    unsure, tokens = classify("What were the total fees paid to KPMG for fiscal 2025?", "cascade", llm, "m")
    assert llm.calls == 1 and unsure["doc_types"] == ["DEF 14A"] and tokens["input_tokens"] == 300
    assert unsure["reason"].startswith("rule unsure")


def test_llm_doc_types_are_ordered_and_deduplicated():
    llm = FakeLLM(Route(doc_types=["DEF 14A", "10-K", "DEF 14A"], question_type="compare", reason="both"))
    result, _ = classify("anything", "llm", llm, "m")
    assert result["doc_types"] == ["10-K", "DEF 14A"]