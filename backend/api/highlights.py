"""인용 -> PDF 위의 사각형 (decision 030).

두 단계로 칠한다:
- block: 청크의 locations (Phase 2에서 저장한 블록 사각형). 항상 있다. 연하게.
- exact: 답변 문장의 숫자를 그 블록 안에서 찾은 위치, 그리고 그 숫자가 있는 표의 '행' 전체. 진하게.
  표 청크는 블록이 표 전체라서 block만 칠하면 어디를 보라는 건지 모른다.

좌표는 PDF 포인트, 왼쪽 위가 원점 (PyMuPDF). 화면에서는 배율만 곱하면 된다: px = pt * (화면 폭 / 페이지 폭).
여기 함수들은 PDF 없이 테스트할 수 있게 "단어 목록"을 받는다. PDF를 여는 건 locate()뿐이다.
"""

import re

# 답변에서 찾을 숫자: 쉼표가 있는 금액, 소수, 퍼센트, 세 자리 이상 정수. 연도와 서류 이름(10-K)은 뺀다.
# 한두 자리 정수("4 regions")는 페이지 여기저기에 있어서 엉뚱한 곳을 칠한다
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+(?=\s?%)|\d{3,}")
FORM_NAMES = re.compile(r"\b(?:\d{1,2}-[KQ]|DEF\s?14A)\b", re.IGNORECASE)
YEARLIKE = re.compile(r"^(19|20)\d{2}$")


def search_terms(text: str) -> list[str]:
    """PDF에서 찾을 숫자들. 원문 표기 그대로 ("2,273", "24.5") 돌려준다: PDF도 그렇게 적혀 있다."""
    terms = []
    for m in NUMBER.findall(FORM_NAMES.sub(" ", text)):
        if not YEARLIKE.match(m.replace(",", "")) and m not in terms:
            terms.append(m)
    return terms


def computed_terms(computed_text: str) -> list[str]:
    """계산 출처에서 PDF에 실제로 적힌 숫자만: 'A -> B' 양쪽 값. 기여도(+911 등)는 PDF에 없다."""
    terms = []
    for left, right in re.findall(r"([\d,.]+) -> ([\d,.]+)", computed_text):
        for t in (left.rstrip(",."), right.rstrip(",.")):  # "2,273," 처럼 문장 부호가 붙어 온다
            # 첫 줄의 "fiscal 2024 -> 2025"도 같은 모양이다: 연도를 찾으면 표 머리글이 칠해진다
            if NUMBER.fullmatch(t) and not YEARLIKE.match(t.replace(",", "")) and t not in terms:
                terms.append(t)
    return terms


def inside(rect, area, margin: float = 2.0) -> bool:
    x0, y0, x1, y1 = rect
    a0, b0, a1, b1 = area
    return x0 >= a0 - margin and y0 >= b0 - margin and x1 <= a1 + margin and y1 <= b1 + margin


def row_band(hit, words: list, area, tolerance: float = 2.0) -> list[float]:
    """숫자 하나가 있는 '행' 전체 사각형.

    hit: 숫자의 사각형. words: 페이지의 단어들 [(x0, y0, x1, y1, text), ...].
    area: 그 행을 찾을 범위 (청크의 블록). 세로 중심이 hit의 높이 안에 있는 단어들을 모은다.
    """
    top, bottom = hit[1] - tolerance, hit[3] + tolerance
    row = [w for w in words if top <= (w[1] + w[3]) / 2 <= bottom and inside(w[:4], area)]
    if not row:
        return list(hit)
    return [min(w[0] for w in row), min(w[1] for w in row), max(w[2] for w in row), max(w[3] for w in row)]


def block_highlights(locations: list[dict]) -> list[dict]:
    return [{"page": loc["page"], "bbox": [round(v, 1) for v in loc["bbox"]], "level": "block"} for loc in locations]


def locate(pdf, locations: list[dict], terms: list[str], max_hits: int = 6) -> list[dict]:
    """pdf: 열린 PyMuPDF 문서. locations: 청크의 블록들. terms: 찾을 숫자.

    숫자는 그 청크의 블록 안에서만 찾는다: 같은 숫자가 페이지의 다른 곳에 있어도 칠하지 않는다.
    """
    highlights = block_highlights(locations)
    seen = set()
    for loc in locations:
        if len([h for h in highlights if h["level"] == "exact"]) >= max_hits:
            break
        page = pdf[loc["page"] - 1]
        words = [tuple(w[:5]) for w in page.get_text("words")]
        for term in terms:
            for rect in page.search_for(term, clip=loc["bbox"]):
                band = row_band(list(rect), words, loc["bbox"])
                key = (loc["page"], tuple(round(v) for v in band))
                if key not in seen:
                    seen.add(key)
                    highlights.append({"page": loc["page"], "bbox": [round(v, 1) for v in band], "level": "exact"})
    return highlights