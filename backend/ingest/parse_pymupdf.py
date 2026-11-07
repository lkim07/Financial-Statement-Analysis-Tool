
"""PyMuPDF baseline parser: PDF pages -> JSON blocks with page, bbox, text.

  Usage: python backend/ingest/parse_pymupdf.py <pdf> <page> [<page> ...]
"""

import json
import sys
from pathlib import Path

import pymupdf


def parse_page(page: pymupdf.Page) -> dict:
    """한 페이지를 {page, width, height, blocks} 딕셔너리로 바꾼다."""
    blocks = []
    kept_count = 0
    total = 0
    page_num = page.number + 1  # 이 아래로는 1부터 세는 번호만 쓴다
    for x0, y0, x1, y1, text, block_no, block_type in page.get_text("blocks"):
        # TODO 1: 이미지 블록(block_type == 1)은 건너뛴다
        if block_type == 1:
            continue
        # TODO 2: blocks에 {"id", "bbox", "text"}를 추가한다
        #   - id는 "p030_b005"처럼 페이지와 블록 번호로 만든다 (page.number는 0부터 시작!)
        #   - bbox는 소수점 1자리로 반올림한 리스트
        #   - text는 앞뒤 공백을 제거한 것
        else:
            total += 1
            text = text.strip()
            rounded = [round(v, 1) for v in (x0, y0, x1, y1)]
            if is_noise(text, rounded, page_num):
                continue
            
            blocks.append({
                "id": f"p{page_num:03d}_b{block_no:03d}", 
                "bbox":rounded, 
                "text":text})
                
            kept_count += 1

    print(f"p{page_num:03d}: {total} blocks -> {kept_count} kept")

    return {
        "page": page_num,  # TODO 3: 1부터 시작하는 페이지 번호
        "width": round(page.rect.width, 1),
        "height": round(page.rect.height, 1),
        "blocks": blocks,
    }


# 그리고 몇 개를 지웠는지 세서 출력해 봐. 
# 예: p030: 20 blocks -> 17 kept. 
# 규칙을 바꿀 때마다 숫자가 어떻게 변하는지 보면, 규칙이 너무 많이 지우는지 적게 지우는지 감이 와요.

# 확인 질문도 하나 할게. 표 머리글 조각을 지우지 않고 남겨 두면, Phase 3 검색에서 어떤 문제가 생길까? 
# 예를 들어 "2025\n2024"만 든 청크가 있다고 상상해 봐.
def is_noise(text: str, bbox: list[float], page_num: int) -> bool:
    """검색에 쓸모없는 블록이면 True."""
    # TODO: 위 세 규칙을 코드로
    # 머리글: 텍스트가 "Table of Contents"이고 페이지 위쪽에 있다
    if text=="Table of Contents" and bbox[1]<50:
        return True
    # 바닥글: 텍스트가 그 페이지 번호와 같다
    if text == str(page_num):
        return True
    # 빈 블록: strip() 후 텍스트가 비었다
    if text == "":
        return True

    return False


def main(pdf_path: str, pages: list[int], out_dir: str) -> None:
    doc = pymupdf.open(pdf_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for p in pages:
        # TODO 4: 1부터 시작하는 번호 p로 페이지를 가져와 parse_page에 넣고,
        #         out_dir/page_030.json 같은 이름으로 저장한다 (한글이 깨지지 않게 ensure_ascii=False)

        with open(out / f"page_{p:03d}.json", "w", encoding="utf-8") as f:
            json.dump(parse_page(doc[p - 1]), f, ensure_ascii=False, indent=2)

 


if __name__ == "__main__":
    # 예: python backend/ingest/parse_pymupdf.py data/amat_10k_fy2025.pdf 30 32 47
    main(sys.argv[1], [int(p) for p in sys.argv[2:]], "data/parsed/pymupdf")



# python f-string zero padding
# python is vs ==
# python list comprehension
# python strip string
# python json.dump ensure_ascii
# pymupdf get_text blocks → 공식 문서(pymupdf.readthedocs.io)를 먼저 봐

# 더 좋은 공부법: 코드를 쓰기 전에 데이터부터 본다

# TODO 4 → 3 → 1 → 2 순서로 바깥에서 안쪽으로 생각한 건 좋은 방법이에요. 하나만 더하자. 오늘 막힌 이유는 page, blocks, text 안에 실제로 뭐가 들어 있는지 모른 채 썼기 때문이에요. 쓰기 전에 대화형 파이썬(REPL)에서 직접 만져 봐:

# powershell
# uv run --project backend python
# python
# >>> import pymupdf
# >>> doc = pymupdf.open("data/amat_10k_fy2025.pdf")
# >>> page = doc[29]          # 30페이지
# >>> page.number
# >>> blocks = page.get_text("blocks")
# >>> blocks[0]               # 튜플 하나가 어떻게 생겼나?
# >>> type(page), type(blocks[0][4])
# >>> f"p{page.number + 1:03d}"

# 한 줄씩 쳐 보면서 결과를 보면, 각 TODO에 뭘 써야 할지가 거의 보여요. 그다음 순서는 이렇게 해:

# TODO 하나를 쓴다.
# 바로 실행한다.
# 에러가 나면 마지막 줄부터 읽는다. 에러 종류(TypeError, SyntaxError 등)와 몇 번째 줄인지가 거기 있어요.
# 다음 TODO로 간다.

# 네 개를 다 쓰고 한 번에 실행하면 에러가 여러 개 겹쳐서 원인을 찾기 어려워요.