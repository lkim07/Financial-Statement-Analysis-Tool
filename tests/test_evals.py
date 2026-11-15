"""평가셋 도구 테스트: 형식 검사와 합치기."""
import json

from merge_golden import read_rows
from validate import validate_row

GOOD = {
    "id": "q01", "type": "why", "question": "q", "answer": "a",
    "evidence": [{"page": 30, "quote": "Net income", "alternatives": [{"page": 47, "quote": "Net income"}]}],
    "numbers": {"net_income_2025": 6998},
}


def test_valid_row_has_no_errors():
    assert validate_row(GOOD) == []


def test_missing_field_and_bad_type():
    row = {k: v for k, v in GOOD.items() if k != "answer"}
    assert any("answer" in e for e in validate_row(row))
    assert any("type" in e for e in validate_row({**GOOD, "type": "guess"}))


def test_unanswerable_must_have_no_evidence():
    assert validate_row({**GOOD, "type": "unanswerable"})


def test_bad_alternative_is_reported():
    bad = {**GOOD, "evidence": [{"page": 30, "quote": "x", "alternatives": [{"page": 0, "quote": ""}]}]}
    errors = validate_row(bad)
    assert any("alternatives[0].page" in e for e in errors)
    assert any("alternatives[0].quote" in e for e in errors)


def test_read_rows_splits_glued_lines(tmp_path):
    a, b = json.dumps({"id": "q10"}), json.dumps({"id": "q11"})
    path = tmp_path / "g.jsonl"
    path.write_text(f"{a}{b}\n", encoding="utf-8")  # 줄바꿈이 빠져 두 객체가 한 줄에 붙은 경우
    assert [r["id"] for r in read_rows(path)] == ["q10", "q11"]