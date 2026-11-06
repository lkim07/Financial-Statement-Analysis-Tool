"""golden.jsonl 검증 스크립트.

사용법:
    python evals/validate.py evals/golden.jsonl
    python evals/validate.py evals/golden.jsonl --pdf data/amat_10k_fy2025.pdf

1단계(항상): 각 줄이 JSON인지, 필수 필드와 타입이 맞는지 검사한다.
2단계(--pdf를 주면): evidence의 quote가 정말 그 page에 있는지 PDF에서 확인한다.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# 질문 유형은 이 네 가지만 허용한다. 라우터를 평가할 때 정답 라벨로 쓸 값이다.
VALID_TYPES = {"why", "numeric", "compare", "unanswerable"}

# 필드 이름 -> 기대하는 파이썬 타입
REQUIRED_FIELDS = {
    "id": str,
    "type": str,
    "question": str,
    "answer": str,
    "evidence": list,
    "numbers": dict,
}


def validate_row(row: dict) -> list[str]:
    """한 줄(질문 하나)을 검사하고, 발견한 문제를 문자열 목록으로 돌려준다."""
    errors = []

    # 1) 필수 필드가 있고 타입이 맞는가
    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in row:
            errors.append(f"'{field}' 필드가 없음")
        elif not isinstance(row[field], expected_type):
            errors.append(f"'{field}'는 {expected_type.__name__}이어야 함")

    # 타입이 틀렸으면 아래 검사는 의미가 없으니 여기서 멈춘다
    if errors:
        return errors

    # 2) type 값이 허용된 것인가
    if row["type"] not in VALID_TYPES:
        errors.append(f"type '{row['type']}'은 {sorted(VALID_TYPES)} 중 하나여야 함")

    # 3) 답 없는 질문은 근거가 비어 있어야 하고, 나머지는 근거가 최소 1개 있어야 한다
    if row["type"] == "unanswerable" and row["evidence"]:
        errors.append("unanswerable인데 evidence가 있음")
    if row["type"] != "unanswerable" and not row["evidence"]:
        errors.append("evidence가 비어 있음")

    # 4) evidence 하나하나가 page(양의 정수)와 quote(비어 있지 않은 문자열)를 갖는가
    for i, ev in enumerate(row["evidence"]):
        page = ev.get("page")
        if not isinstance(page, int) or page < 1:
            errors.append(f"evidence[{i}].page는 1 이상의 정수여야 함")
        if not ev.get("quote"):
            errors.append(f"evidence[{i}].quote가 비어 있음")

    # 5) numbers의 값은 모두 숫자여야 한다 (bool은 int의 하위 타입이라 따로 막는다)
    for key, value in row["numbers"].items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"numbers['{key}']가 숫자가 아님")

    return errors


def normalize(text: str) -> str:
    """공백, 줄바꿈을 한 칸으로 합친다. PDF 텍스트는 줄바꿈 위치가 제멋대로라서 필요하다."""
    return re.sub(r"\s+", " ", text).strip()


def check_quotes(rows: list[dict], pdf_path: Path) -> list[str]:
    """evidence의 quote가 적힌 page에 실제로 있는지 PDF를 열어 확인한다."""
    import pymupdf  # --pdf를 쓸 때만 필요하므로 여기서 import한다

    doc = pymupdf.open(pdf_path)
    # 페이지 텍스트를 한 번만 뽑아 둔다. page는 1부터, 리스트 인덱스는 0부터 시작한다.
    pages = [normalize(page.get_text()) for page in doc]

    errors = []
    for row in rows:
        for ev in row.get("evidence", []):
            page = ev.get("page")
            if not isinstance(page, int) or not 1 <= page <= len(pages):
                errors.append(f"{row['id']}: page {page}는 PDF 범위(1~{len(pages)}) 밖")
                continue
            if normalize(ev["quote"]) not in pages[page - 1]:
                errors.append(f"{row['id']}: p{page}에서 quote를 찾지 못함 -> {ev['quote']!r}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="golden.jsonl 검증")
    parser.add_argument("path", type=Path)
    parser.add_argument("--pdf", type=Path, help="quote를 대조할 원본 PDF")
    args = parser.parse_args()

    rows, errors, seen_ids = [], [], set()

    # jsonl은 "한 줄 = JSON 하나"이므로 줄 단위로 읽는다
    with args.path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue  # 빈 줄은 건너뛴다
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"{line_no}번째 줄: JSON 파싱 실패 ({e.msg})")
                continue

            for err in validate_row(row):
                errors.append(f"{line_no}번째 줄 ({row.get('id', '?')}): {err}")

            # id가 겹치면 평가 결과를 질문에 매칭할 수 없다
            if row.get("id") in seen_ids:
                errors.append(f"{line_no}번째 줄: id '{row['id']}' 중복")
            seen_ids.add(row.get("id"))
            rows.append(row)

    if args.pdf:
        errors += check_quotes(rows, args.pdf)

    if errors:
        print(f"문제 {len(errors)}개 발견:")
        for err in errors:
            print("  -", err)
        return 1  # 0이 아닌 종료 코드 = 실패. 나중에 CI가 이 값을 보고 빌드를 멈춘다

    counts = {t: sum(r["type"] == t for r in rows) for t in sorted(VALID_TYPES)}
    print(f"OK: 질문 {len(rows)}개, 유형별 {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())