"""LLM 출력과 계산 출처를 코드로 다루는 순수 함수들 (CI에서 OpenAI/Milvus 없이 테스트한다).

- computed_source: 브리지를 LLM이 인용할 수 있는 '계산 출처'로 (decision 024)
- ungrounded_numbers: 답변 문장의 숫자가 인용한 출처에 실제로 있는지
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "numbers"), str(ROOT / "ingest")]
from bridge import load_facts, net_income_bridge  # noqa: E402
from registry import doc_label, get_doc  # noqa: E402


def computed_source(p: dict, facts_loader=None, label_of=None) -> dict:
    """브리지를 LLM이 인용할 수 있는 '계산 출처' 하나로 만든다. 숫자는 전부 코드가 계산했다."""
    facts = (facts_loader or load_facts)(p["bridge_doc"])
    b = net_income_bridge(facts, p["year"])
    label = (label_of or (lambda d: doc_label(get_doc(d))))(p["bridge_doc"])
    pages = sorted({d["page"] for d in b["drivers"] if d["page"]})
    lines = [
        f"Net income bridge, fiscal {b['prior_year']} -> {b['year']} (USD millions), computed by code "
        f"from the {label} income statement (page {', '.join(map(str, pages))}).",
        "Contribution = change in the item; cost and tax increases count as negative.",
        f"Net income: {b['net_income_prior']:,.0f} -> {b['net_income']:,.0f} (change {b['change']:+,.0f})",
    ]
    lines += [f"- {d['label'].split(': ')[-1]}: {d['prior']:,.0f} -> {d['current']:,.0f}, "
              f"contribution {d['contribution']:+,.0f}" for d in b["drivers"]]
    lines.append(f"Contributions sum to {sum(d['contribution'] for d in b['drivers']):+,.0f}.")
    return {"chunk_id": f"calc:bridge:{p['bridge_doc']}:{p['year']}", "doc_id": p["bridge_doc"],
            "doc_label": f"COMPUTED from {label}", "page": pages[0] if pages else None,
            "heading": "net income bridge", "text": "\n".join(lines), "computed": True,
            # 출처의 출처: 이 계산에 쓴 손익계산서 청크. 대시보드는 이 청크의 행을 하이라이트한다
            "source_chunks": sorted({d["chunk_id"] for d in b["drivers"] if d["chunk_id"]})}


def expand_citations(cited: set[str], sources: list[dict]) -> set[str]:
    """계산 출처를 인용했으면, 그 계산에 쓴 청크도 인용한 것으로 본다 (근거 추적용)."""
    expanded = set(cited)
    for s in sources:
        if s.get("computed") and s["chunk_id"] in cited:
            expanded.update(s.get("source_chunks", []))
    return expanded


# 숫자 검사에서 빼는 것:
# - 연도(2024 등): "fiscal 2025"는 출처에 그대로 없어도 틀린 말이 아니다
# - 공시 서류 이름(10-K, 8-K, 10-Q, DEF 14A): 숫자가 아니라 이름이다
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
YEARLIKE = re.compile(r"^(19|20)\d{2}$")
FORM_NAMES = re.compile(r"\b(?:\d{1,2}-[KQ]|DEF\s?14A)\b", re.IGNORECASE)


def numbers_of(text: str) -> set[str]:
    out = set()
    for m in NUMBER.findall(FORM_NAMES.sub(" ", text)):
        n = m.replace(",", "").rstrip(".")
        if n and not YEARLIKE.match(n):
            out.add(n)
    return out


def ungrounded_numbers(answer, chunks: list[dict]) -> list[str]:
    """인용한 출처에 없는 숫자. inferred 문장(LLM이 직접 계산)은 뺀다.

    LLM 판정관보다 먼저 도는 싸고 확실한 검사: "1,298"이 인용한 청크에 없으면 판정할 필요도 없다.
    """
    text_of = {c["chunk_id"]: c["text"] for c in chunks}
    problems = []
    for i, s in enumerate(answer.sentences, start=1):
        if s.inferred:
            continue
        cited = numbers_of(" ".join(text_of.get(cid, "") for cid in s.citations))
        missing = sorted(numbers_of(s.text) - cited)
        if missing:
            problems.append(f"sentence {i}: numbers not in cited sources: {', '.join(missing)}")
    return problems