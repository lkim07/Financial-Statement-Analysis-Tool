"""LlamaParse parser, split into a paid step (fetch) and a free step (convert).

Usage:
  uv run --project backend --env-file .env python backend/ingest/parse_llamaparse.py fetch --doc amat-10k-fy2024 [--pages 1-102]
  uv run --project backend python backend/ingest/parse_llamaparse.py convert --doc amat-10k-fy2024
Writes: data/parsed/<doc_id>/llamaparse/raw.json (fetch), page_NNN.json (convert)
"""

import argparse
import json
import re

from parse_pymupdf import is_noise
from registry import get_doc, parsed_dir, pdf_path


def fetch(doc: dict, pages: str | None) -> None:
    """API를 호출해서 응답을 가공하지 않고 저장한다. 크레딧이 든다."""
    import pymupdf
    from llama_cloud import LlamaCloud

    pdf = pdf_path(doc)
    pages = pages or f"1-{pymupdf.open(pdf).page_count}"
    client = LlamaCloud()  # LLAMA_CLOUD_API_KEY 환경 변수를 읽는다
    file = client.files.create(file=str(pdf), purpose="parse")
    result = client.parsing.parse(
        file_id=file.id,
        tier="cost_effective",
        version="latest",
        page_ranges={"target_pages": pages},
        expand=["items", "markdown"],
    )
    out = parsed_dir(doc, "llamaparse")
    out.mkdir(parents=True, exist_ok=True)
    (out / "raw.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(f"Raw LlamaParse response saved to: {out / 'raw.json'}")


def to_blocks(raw: dict) -> list[dict]:
    """raw.json -> PyMuPDF와 같은 형식의 페이지 리스트."""
    pages_out = []
    for page in raw["items"]["pages"]:
        page_num = page["page_number"]
        blocks, total = [], 0
        for i, item in enumerate(page.get("items", [])):
            total += 1
            boxes = item.get("bbox") or []
            if not boxes:
                continue
            b = boxes[0]
            bbox = [round(v, 1) for v in (b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"])]

            md = (item.get("md") or "").strip()
            plain = item.get("value")
            if not isinstance(plain, str):
                plain = md.lstrip("#").strip()
            plain = re.sub(r"<[^>]+>", "", plain).strip()

            if is_noise(plain, bbox, page_num):
                continue
            blocks.append({"id": f"p{page_num:03d}_i{i:03d}", "type": item.get("type", "unknown"),
                           "bbox": bbox, "text": md})

        print(f"p{page_num:03d}: {total} items -> {len(blocks)} kept")
        pages_out.append({"page": page_num, "width": round(page["page_width"], 1),
                          "height": round(page["page_height"], 1), "blocks": blocks})
    return pages_out


def convert(doc: dict) -> None:
    """raw.json을 읽어 페이지별 JSON으로 바꾼다. API를 부르지 않는다."""
    out = parsed_dir(doc, "llamaparse")
    raw = json.loads((out / "raw.json").read_text(encoding="utf-8"))
    for page in to_blocks(raw):
        with open(out / f"page_{page['page']:03d}.json", "w", encoding="utf-8") as f:
            json.dump(page, f, ensure_ascii=False, indent=2)
    print(f"saved -> {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["fetch", "convert"])
    parser.add_argument("--doc", required=True)
    parser.add_argument("--pages", help="e.g. 30,32,47 or 1-102 (fetch only; default: all pages)")
    args = parser.parse_args()
    doc = get_doc(args.doc)
    fetch(doc, args.pages) if args.command == "fetch" else convert(doc)
