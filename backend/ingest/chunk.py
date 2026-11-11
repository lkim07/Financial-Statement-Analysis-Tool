"""Parsed pages -> chunks (v2.1: merge short blocks, split long ones, attach table captions).

Usage: uv run --project backend python backend/ingest/chunk.py
Reads:  data/parsed/llamaparse/page_*.json
Writes: data/chunks/llamaparse.jsonl

Rules (see docs/decisions.md 010):
- Heading blocks are not chunks. They become each chunk's `heading` and context header.
- Consecutive text/list blocks under the same heading are merged up to MERGE_TARGET tokens,
  across page boundaries.
- Tables are never merged with text. Long tables are split into row groups; every piece
  repeats the table's header row and the most recent section-label row.
- Long text/list blocks are split at sentence or line boundaries.
- Short captions right before a table (ending with ':' or '(In millions ...)') are attached
  to every piece of that table. Repeated boilerplate ('See accompanying Notes ...') is dropped.
"""

import collections
import json
import re
import statistics
from pathlib import Path

from sentence_transformers import SentenceTransformer

DOC_NAME = "Applied Materials FY2025 10-K"
EXHIBIT_START_PAGE = 88  # p88부터는 첨부 서류(Exhibit)
MODEL_MAX_TOKENS = 512  # bge-small이 한 번에 읽는 최대 길이
EMBED_LIMIT = 500  # embed_text(머리글 포함) 한도. 모델이 붙이는 특수 토큰 자리를 남긴다
MERGE_TARGET = 400  # 짧은 블록을 합칠 때 본문(text)의 목표 크기
MIN_TOKENS = 20  # 본문이 이보다 짧으면 "너무 짧음"으로 보고한다
SKIP_TYPES = {"header", "footer"}  # 노이즈 필터를 빠져나온 머리글/바닥글
BOILERPLATE = ("See accompanying Notes",)  # 재무제표 아래마다 반복되는 문구
CAPTION_MAX_TOKENS = 40  # 표 바로 앞의 짧은 설명문만 캡션으로 본다


class Chunker:
    """블록을 문서 순서대로 받아서 청크를 만든다.

    합치는 중인 블록들을 buffer에 모아 두었다가, 더 못 넣게 되면 flush()로 청크 하나를 만든다.
    """

    def __init__(self, count_tokens):
        self.count = count_tokens  # 문자열 -> 토큰 수 함수
        self.chunks = []
        self.heading = None
        self.section = "body"
        self.buffer = []  # 합치는 중인 블록들: [{"page", "bbox", "text", "type", "id"}]

    # ---------- 공통 ----------

    def context(self, page: int) -> str:
        """맥락 머리글. 청크가 문서의 어디에서 왔는지 임베딩 모델에게 알려 준다."""
        return f"{DOC_NAME} | p{page} | {self.heading or ''}"

    def body_limit(self, page: int) -> int:
        """본문에 쓸 수 있는 토큰 수 = 전체 한도 - 머리글 길이 - 줄바꿈 여유."""
        return EMBED_LIMIT - self.count(self.context(page)) - 2

    def emit(self, parts: list[dict], text: str, chunk_type: str) -> None:
        """청크 하나를 만든다. parts는 이 청크를 이루는 원본 블록들(위치 정보용)."""
        first_page = parts[0]["page"]
        self.chunks.append({
            "chunk_id": f"c{len(self.chunks):04d}",
            "page": first_page,
            "section": "exhibit" if first_page >= EXHIBIT_START_PAGE else "body",
            "heading": self.heading,
            "type": chunk_type,
            # 페이지를 넘어 합칠 수 있으므로 위치는 (페이지, bbox) 목록이다. 하이라이트에 쓴다
            "locations": [{"page": p["page"], "bbox": p["bbox"]} for p in parts],
            "source_ids": [p["id"] for p in parts],
            "text": text,
            "embed_text": f"{self.context(first_page)}\n{text}",
        })

    # ---------- 합치기 (text, list) ----------

    def flush(self) -> None:
        """buffer에 모인 블록들을 청크 하나로 만든다."""
        if not self.buffer:
            return
        types = {b["type"] for b in self.buffer}
        chunk_type = types.pop() if len(types) == 1 else "text"
        text = "\n\n".join(b["text"] for b in self.buffer)
        self.emit(self.buffer, text, chunk_type)
        self.buffer = []

    def add_text(self, block: dict) -> None:
        limit = min(MERGE_TARGET, self.body_limit(block["page"]))
        n = self.count(block["text"])

        # 블록 하나가 한도보다 길면 잘라서 조각마다 따로 넣는다
        if n > limit:
            self.flush()
            for piece in split_text(block["text"], block["type"], limit, self.count):
                self.buffer = [{**block, "text": piece}]
                self.flush()
            return

        # 지금 buffer에 더하면 목표를 넘는다면, 먼저 지금까지 모은 것을 청크로 만든다
        current = sum(self.count(b["text"]) for b in self.buffer)
        if self.buffer and current + n > limit:
            self.flush()
        self.buffer.append(block)

    # ---------- 자르기 (table) ----------

    def add_table(self, block: dict) -> None:
        # 표 바로 앞의 짧은 설명문("... were as follows:", "(In millions)")은 표의 캡션이다.
        # 표와 떨어지면 둘 다 의미가 약해지므로, buffer 끝에서 꺼내 표 조각마다 붙인다
        captions = []
        while self.buffer and is_caption(self.buffer[-1]["text"], self.count):
            captions.insert(0, self.buffer.pop())
        self.flush()  # 남은 텍스트는 따로 청크로. 표는 일반 텍스트와 합치지 않는다

        caption_text = "\n".join(c["text"] for c in captions)
        limit = self.body_limit(block["page"]) - (self.count(caption_text) + 1 if captions else 0)
        for piece in split_table(block["text"], limit, self.count):
            text = f"{caption_text}\n{piece}" if captions else piece
            self.emit(captions + [block], text, "table")

    # ---------- 진입점 ----------

    def add(self, block: dict, page: int) -> None:
        block = {**block, "page": page}
        block_type = block["type"]

        # 본문 -> 첨부 서류로 넘어가면 앞 내용과 합치지 않고, 본문의 제목도 이어받지 않는다
        section = "exhibit" if page >= EXHIBIT_START_PAGE else "body"
        if section != self.section:
            self.flush()
            self.heading = None
            self.section = section

        if block_type in SKIP_TYPES or block["text"].startswith(BOILERPLATE):
            return
        if block_type == "heading":
            self.flush()  # 제목이 바뀌면 그 전까지 모은 것은 이전 제목 아래 청크다
            self.heading = clean_heading(block["text"])
            return
        if block_type == "table":
            self.add_table(block)
        else:
            self.add_text(block)


def clean_heading(md: str) -> str:
    """'# <u>CONSOLIDATED ...</u>' -> 'CONSOLIDATED ...'"""
    text = md.lstrip("#").strip()
    return re.sub(r"<[^>]+>", "", text).strip()


def split_text(text: str, block_type: str, limit: int, count) -> list[str]:
    """긴 text/list를 limit 토큰 이하 조각으로 자른다.

    list는 줄(항목) 단위, text는 문장 단위로 자른다. 단위 하나가 limit보다 길면 그대로 둔다(드묾).
    """
    if block_type == "list":
        units = [line for line in text.split("\n") if line.strip()]
        joiner = "\n"
    else:
        # 마침표/물음표/느낌표 뒤의 공백에서 자른다. (?<=...)는 "앞에 이게 있는 자리"라는 뜻
        units = re.split(r"(?<=[.!?])\s+", text)
        joiner = " "

    pieces, current = [], []
    for unit in units:
        if current and count(joiner.join(current + [unit])) > limit:
            pieces.append(joiner.join(current))
            current = []
        current.append(unit)
    if current:
        pieces.append(joiner.join(current))
    return pieces


def is_caption(text: str, count) -> bool:
    """표 앞에 붙는 짧은 설명문인가: ':'로 끝나는 문장, 또는 '(In millions ...)' 단위 표기."""
    plain = text.strip().strip("*").strip()
    return count(plain) <= CAPTION_MAX_TOKENS and (plain.endswith(":") or plain.startswith("(In "))


def is_label_row(row: str) -> bool:
    """'| Weighted average number of shares: |  |  |  |' 처럼 첫 칸만 있고 나머지가 빈 행."""
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    return len(cells) > 1 and cells[0] != "" and all(c == "" for c in cells[1:])


def split_table(md: str, limit: int, count) -> list[str]:
    """긴 마크다운 표를 행 묶음으로 자른다.

    조각마다 (1) 표의 머리글 행과 구분선, (2) 직전 섹션 행을 다시 붙여서
    조각 하나만 봐도 '어느 해의 무슨 값인지' 알 수 있게 한다.
    """
    lines = [line for line in md.split("\n") if line.strip()]
    if count(md) <= limit:
        return [md]

    # 둘째 줄이 '| --- | --- |' 구분선이면 앞 두 줄이 머리글이다
    has_header = len(lines) > 1 and set(lines[1].replace("|", "").strip()) <= set("-: ")
    header = lines[:2] if has_header else []
    rows = lines[2:] if has_header else lines

    pieces, current, label = [], [], None
    for row in rows:
        # 이 행을 더하면 한도를 넘는다면, 지금까지를 한 조각으로 끊는다
        if current and count("\n".join(header + current + [row])) > limit:
            # 조각이 섹션 행으로 끝나면 그 행은 내용 없이 매달린 셈이니 다음 조각으로 넘긴다
            carry = [current.pop()] if len(current) > 1 and is_label_row(current[-1]) else []
            pieces.append("\n".join(header + current))
            # 새 조각을 시작한다. 직전 섹션 행이 있으면 맨 앞에 다시 붙인다
            # (새로 시작하는 행이 섹션 행 자신이면 붙이지 않는다)
            current = carry or ([label] if label and not is_label_row(row) else [])
        current.append(row)
        if is_label_row(row):
            label = row
    if current:
        pieces.append("\n".join(header + current))
    return pieces


def make_chunks(pages: list[dict], count_tokens) -> list[dict]:
    chunker = Chunker(count_tokens)
    for page in pages:
        for block in page["blocks"]:
            chunker.add(block, page["page"])
    chunker.flush()  # 마지막에 남은 것
    return chunker.chunks


def report(chunks: list[dict], count_tokens) -> None:
    """청크 통계. 512 초과는 embed_text로, 너무 짧음은 본문(text)으로 잰다."""
    embed_lengths = [count_tokens(c["embed_text"]) for c in chunks]
    text_lengths = [count_tokens(c["text"]) for c in chunks]

    print(f"chunks: {len(chunks)}")
    print("by type:", dict(collections.Counter(c["type"] for c in chunks)))
    print("cross-page chunks:", sum(len({loc['page'] for loc in c["locations"]}) > 1 for c in chunks))
    print(f"text tokens:  min {min(text_lengths)}, median {statistics.median(text_lengths)}, max {max(text_lengths)}")
    print(f"embed tokens: min {min(embed_lengths)}, median {statistics.median(embed_lengths)}, max {max(embed_lengths)}")

    too_long = [(c, n) for c, n in zip(chunks, embed_lengths) if n > MODEL_MAX_TOKENS]
    too_short = [(c, n) for c, n in zip(chunks, text_lengths) if n < MIN_TOKENS]

    print(f"\n> {MODEL_MAX_TOKENS} embed tokens (뒷부분이 잘림): {len(too_long)}")
    for c, n in too_long:
        print(f"  {c['chunk_id']}  p{c['page']:<3} {c['type']:<6} {n}")

    print(f"\n< {MIN_TOKENS} text tokens (너무 짧음): {len(too_short)}")
    for c, n in too_short[:20]:
        print(f"  {c['chunk_id']}  p{c['page']:<3} {c['type']:<6} {n}  {c['text'][:50]!r}")


def save_jsonl(chunks: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"\nsaved {len(chunks)} chunks -> {path}")


if __name__ == "__main__":
    parsed_dir = Path("data/parsed/llamaparse")
    pages = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(parsed_dir.glob("page_*.json"))]

    model = SentenceTransformer("BAAI/bge-small-en-v1.5")
    tokenizer = model.tokenizer

    def count_tokens(text: str) -> int:
        # 특수 토큰([CLS], [SEP])은 빼고 센다. EMBED_LIMIT에 그 자리를 남겨 두었다
        return len(tokenizer.encode(text, add_special_tokens=False))

    chunks = make_chunks(pages, count_tokens)
    report(chunks, count_tokens)
    save_jsonl(chunks, Path("data/chunks/llamaparse.jsonl"))