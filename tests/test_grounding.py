"""계산 출처와 숫자 검사 테스트 (OpenAI/Milvus 없이)."""
from types import SimpleNamespace as NS

from extract import extract_income_statement
from grounding import computed_source, expand_citations, numbers_of, ungrounded_numbers
from test_numbers import CHUNK

FACTS = extract_income_statement([CHUNK], "amat-10k-fy2025")
PLAN = {"bridge_doc": "amat-10k-fy2025", "year": 2025}


def test_computed_source_lists_every_driver():
    src = computed_source(PLAN, facts_loader=lambda d: FACTS, label_of=lambda d: "FY2025 10-K")
    assert src["chunk_id"] == "calc:bridge:amat-10k-fy2025:2025"
    assert "Provision for income taxes: 975 -> 2,273, contribution -1,298" in src["text"]
    assert "Net income: 7,177 -> 6,998 (change -179)" in src["text"]
    assert "Contributions sum to -179." in src["text"]
    assert src["page"] == 47
    assert src["source_chunks"] == ["amat-10k-fy2025:c0155"]


def test_citing_the_bridge_counts_as_citing_its_statement_chunk():
    src = computed_source(PLAN, facts_loader=lambda d: FACTS, label_of=lambda d: "FY2025 10-K")
    assert expand_citations({src["chunk_id"], "c9"}, [src]) == {src["chunk_id"], "c9", "amat-10k-fy2025:c0155"}
    assert expand_citations({"c9"}, [src]) == {"c9"}


def test_numbers_of_ignores_years_and_formatting():
    assert numbers_of("In fiscal 2025, taxes rose to $2,273 million (24.5%).") == {"2273", "24.5"}


def test_numbers_of_ignores_form_names():
    # 첫 실행에서 "The FY2025 10-K says ..."의 10이 '출처에 없는 숫자'로 잡혔다
    assert numbers_of("The FY2025 10-K and the 8-K, 10-Q and DEF 14A filings report 659 million.") == {"659"}


def sentence(text, cites, inferred=False):
    return NS(text=text, citations=cites, inferred=inferred)


def test_ungrounded_numbers_flags_numbers_missing_from_cited_sources():
    chunks = [{"chunk_id": "c1", "text": "Provision for income taxes 2,273 975"},
              {"chunk_id": "c2", "text": "Gross profit 13,808 12,897"}]
    answer = NS(sentences=[
        sentence("The tax provision rose from $975 million to $2,273 million.", ["c1"]),
        sentence("The tax provision rose by about $1.3 billion.", ["c1"]),          # 반올림: 출처에 없음
        sentence("Gross profit was 13,808.", ["c1"]),                               # 다른 청크의 숫자
        sentence("Taxes rose by 1,298, more than gross profit's 911 gain.", ["c1"], inferred=True),
    ])
    problems = ungrounded_numbers(answer, chunks)
    assert len(problems) == 2
    assert problems[0].startswith("sentence 2") and "1.3" in problems[0]
    assert problems[1].startswith("sentence 3") and "13808" in problems[1]