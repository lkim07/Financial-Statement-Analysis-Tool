"""LlamaParse parser: saves the raw API response for selected pages.

Usage: uv run --project backend --env-file .env python backend/ingest/parse_llamaparse.py <pdf> <pages>
Example: ... data/amat_10k_fy2025.pdf 30,32,47,80
"""
import sys
import json
from pathlib import Path

from llama_cloud import LlamaCloud

from parse_pymupdf import is_noise
import re


def main(pdf_path: str, pages: str, out_dir: str) -> None:
    client = LlamaCloud()  # 환경 변수 LLAMA_CLOUD_API_KEY를 읽는다

    # 1) 파일을 LlamaCloud에 업로드한다
    file = client.files.create(file=pdf_path, purpose="parse")

    # 2) 지정한 페이지만 파싱한다 (페이지 번호는 1부터, 쉼표로 구분)
    result = client.parsing.parse(
        file_id=file.id,
        tier="cost_effective",
        version="latest",
        page_ranges={"target_pages": pages},
        expand=["items", "markdown"],
    )

    # 3) TODO: 가공하지 않은 응답 전체를 out_dir/raw.json으로 저장한다
    #    힌트: result는 pydantic 모델이라 result.model_dump_json(indent=2)로 JSON 문자열을 얻을 수 있다
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "raw.json"
    output_path.write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8"
    )

    print(f"Raw LlamaParse response saved to: {output_path}")

# def to_blocks(raw: dict) -> list[dict]:
#     """raw.json -> [{page, width, height, blocks}, ...] (PyMuPDF와 같은 형식)"""
#     # TODO: 페이지마다, 아이템마다
#     #   - id: "p030_i004"처럼 (i = item, PyMuPDF의 b와 구분)
#     #   - type: 아이템의 type을 그대로 (PyMuPDF에는 없던 정보!)
#     #   - bbox: 첫 번째 박스를 [x0, y0, x1, y1]로 변환, 반올림 1자리
#     #   - text: 아이템의 md
#     #   - 노이즈 필터도 적용 (parse_pymupdf의 is_noise를 import해서 재사용)
    
#     # parse_llamaparse.py에 raw.json을 읽어서 PyMuPDF와 같은 형식으로 바꾸는 함수를 추가해 봐. API는 다시 부르지 않아요:

#     # 결과는 data/parsed/llamaparse/page_030.json처럼 페이지별로 저장해. 그러면 두 폴더의 같은 이름 파일을 나란히 열어 비교할 수 있어요.

#     # 지금 is_noise의 세 규칙이 표 아이템을 실수로 지울 가능성이 있을까? 코드를 돌려 보기 전에 먼저 예측해 봐.
#     ...
def to_blocks(raw: dict) -> list[dict]:
    """raw.json(LlamaParse 응답) -> PyMuPDF와 같은 형식의 페이지 리스트.

    반환: [{"page", "width", "height", "blocks": [{"id", "type", "bbox", "text"}]}, ...]
    """
    pages_out = []

    for page in raw["items"]["pages"]:
        page_num = page["page_number"]  # LlamaParse v2는 이미 1부터 센다
        blocks = []
        total = 0

        for i, item in enumerate(page.get("items", [])):
            total += 1

            # 1) bbox: 박스가 없는 아이템도 있을 수 있으니 확인한다
            boxes = item.get("bbox") or []
            if not boxes:
                continue
            b = boxes[0]  # 박스가 여러 개면 일단 첫 번째만 쓴다 (한계: 008에 기록)
            bbox = [round(v, 1) for v in (b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"])]

            # 2) 텍스트는 두 가지를 구분한다
            #    - md:    마크다운 ("# Table of Contents", 표는 "| a | b |")  -> 저장용
            #    - plain: 기호 없는 순수 텍스트                              -> 노이즈 판정용
            md = (item.get("md") or "").strip()
            plain = item.get("value")
            if not isinstance(plain, str):
                plain = md.lstrip("#").strip()
            plain = re.sub(r"<[^>]+>", "", plain).strip()   # <u>, </u> 같은 태그 제거

            # 3) 노이즈 필터는 PyMuPDF와 같은 규칙을 재사용한다
            if is_noise(plain, bbox, page_num):
                continue

            blocks.append({
                "id": f"p{page_num:03d}_i{i:03d}",
                "type": item.get("type", "unknown"),
                "bbox": bbox,
                "text": md,
            })

        print(f"p{page_num:03d}: {total} items -> {len(blocks)} kept")
        pages_out.append({
            "page": page_num,
            "width": round(page["page_width"], 1),
            "height": round(page["page_height"], 1),
            "blocks": blocks,
        })

    return pages_out


def fetch(pdf_path: str, pages: str, out_dir: Path) -> None:
    """API를 호출해서 raw.json을 저장한다. 크레딧이 든다."""
    client = LlamaCloud()
    # ... 지금 main()에 있는 업로드, 파싱, 저장 코드를 여기로 옮긴다
    # 1) Upload PDF
    file = client.files.create(
        file=pdf_path,
        purpose="parse",
    )

    # 2) Parse selected pages
    result = client.parsing.parse(
        file_id=file.id,
        tier="cost_effective",
        version="latest",
        page_ranges={"target_pages": pages},
        expand=["items", "markdown"],
    )

    # 3) Save raw API response
    out_dir.mkdir(parents=True, exist_ok=True)

    output_path = out_dir / "raw.json"
    output_path.write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )

    print(f"Raw LlamaParse response saved to: {output_path}")


def convert(out_dir: Path) -> None:
    """raw.json을 읽어 페이지별 JSON으로 바꾼다. API를 부르지 않는다."""
    # TODO: raw.json 읽기 -> to_blocks() -> 페이지마다 page_NNN.json 저장
    # out_dir / "raw.json"을 읽어서 json.load로 딕셔너리로 만든다
    # to_blocks(raw)를 부른다
    # 돌려받은 리스트를 돌면서 페이지마다 page_NNN.json으로 저장한다. parse_pymupdf.py의 저장 코드와 거의 같아요
    """Read raw.json and save each page as a separate JSON file. No API call."""
    raw_path = out_dir / "raw.json"

    if not raw_path.exists():
        raise FileNotFoundError(
            f"Raw response not found: {raw_path}. Run fetch first."
        )

    # 1) Load raw JSON
    with raw_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    # 2) Convert raw response into page blocks
    pages = to_blocks(raw)

    # 3) Save each page separately
    out_dir.mkdir(parents=True, exist_ok=True)

    for page_data in pages:
        page_num = page_data["page"]
        output_path = out_dir / f"page_{page_num:03d}.json"

        with output_path.open("w", encoding="utf-8") as f:
            json.dump(
                page_data,
                f,
                ensure_ascii=False,
                indent=2,
            )

        print(f"Saved: {output_path}")

if __name__ == "__main__":
    out_dir = Path("data/parsed/llamaparse")
    command = sys.argv[1]
    if command == "fetch":
        fetch(sys.argv[2], sys.argv[3], out_dir)
    elif command == "convert":
        convert(out_dir)
    else:
        print("usage: parse_llamaparse.py fetch <pdf> <pages> | convert")

# # 한 번만 (이미 raw.json이 있으니 지금은 안 해도 됨)
# uv run --project backend --env-file .env python backend/ingest/parse_llamaparse.py fetch data/amat_10k_fy2025.pdf 30,32,47,80

# # 코드 고칠 때마다
# uv run --project backend python backend/ingest/parse_llamaparse.py convert