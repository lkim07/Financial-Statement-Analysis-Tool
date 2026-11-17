"""PyMuPDF baseline parser: PDF pages -> JSON blocks with page, bbox, text.

Usage: python backend/ingest/parse_pymupdf.py --doc amat-10k-fy2024 [page ...]
       (no pages = every page)
Writes: data/parsed/<doc_id>/pymupdf/page_NNN.json
"""

import argparse
import json

import pymupdf

from registry import get_doc, parsed_dir, pdf_path


def parse_page(page: pymupdf.Page) -> dict:
    """한 페이지를 {page, width, height, blocks} 딕셔너리로 바꾼다."""
    blocks = []
    total = 0
    page_num = page.number + 1  # 이 아래로는 1부터 세는 번호만 쓴다
    for x0, y0, x1, y1, text, block_no, block_type in page.get_text("blocks"):
        if block_type == 1:  # 이미지 블록
            continue
        total += 1
        text = text.strip()
        rounded = [round(v, 1) for v in (x0, y0, x1, y1)]
        if is_noise(text, rounded, page_num):
            continue
        blocks.append({"id": f"p{page_num:03d}_b{block_no:03d}", "bbox": rounded, "text": text})

    print(f"p{page_num:03d}: {total} blocks -> {len(blocks)} kept")
    return {
        "page": page_num,
        "width": round(page.rect.width, 1),
        "height": round(page.rect.height, 1),
        "blocks": blocks,
    }


def is_noise(text: str, bbox: list[float], page_num: int) -> bool:
    """검색에 쓸모없는 블록이면 True. 위치만이 아니라 내용과 위치를 같이 본다 (decision 007)."""
    # 머리글: 모든 페이지 위쪽의 "Table of Contents" 링크
    if text == "Table of Contents" and bbox[1] < 50:
        return True
    # 바닥글: 숫자만 있는 짧은 텍스트 (첨부 서류는 페이지 번호가 1부터 다시 시작한다)
    if text.isdigit() and len(text) <= 3:
        return True
    # 빈 블록
    if text == "":
        return True
    return False


def main(doc_id: str, pages: list[int]) -> None:
    doc = get_doc(doc_id)
    pdf = pymupdf.open(pdf_path(doc))
    out = parsed_dir(doc, "pymupdf")
    out.mkdir(parents=True, exist_ok=True)
    for p in pages or range(1, pdf.page_count + 1):
        with open(out / f"page_{p:03d}.json", "w", encoding="utf-8") as f:
            json.dump(parse_page(pdf[p - 1]), f, ensure_ascii=False, indent=2)
    print(f"saved -> {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc", required=True)
    parser.add_argument("pages", nargs="*", type=int)
    args = parser.parse_args()
    main(args.doc, args.pages)
