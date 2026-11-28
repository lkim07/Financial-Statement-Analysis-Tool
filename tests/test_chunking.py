"""chunk.py의 순수 함수 테스트. 모델 없이, 단어 수를 토큰 수 대신 쓴다."""
from chunk import clean_heading, compact_table, is_caption, is_label_row, split_table


def count(text: str) -> int:
    return len(text.split())


TABLE = """| Fiscal Year | 2025 | 2024 |
| ----------------------- | ------- | ------- |
| Net income | $ 6,998 | $ 7,177 |
| Earnings per share: |  |  |
| Basic | $ 8.71 | $ 8.68 |
| Diluted | $ 8.66 | $ 8.61 |
| Weighted average number of shares: |  |  |
| Basic | 804 | 827 |
| Diluted | 808 | 834 |"""


def test_clean_heading_strips_markdown_and_html():
    assert clean_heading("# <u>**CONSOLIDATED STATEMENTS**</u>") == "CONSOLIDATED STATEMENTS"
    assert clean_heading("*Segment Operating Income (Loss)*") == "Segment Operating Income (Loss)"


def test_compact_table_shortens_separator():
    lines = compact_table(TABLE).split("\n")
    assert lines[1] == "| --- | --- | --- |"
    assert lines[2] == "| Net income | $ 6,998 | $ 7,177 |"


def test_is_caption():
    assert is_caption("Net revenue by segment for the periods presented were as follows:", count)
    assert is_caption("**(In millions, except per share amounts)**", count)
    assert not is_caption("Net revenue increased 4% in fiscal 2025.", count)


def test_is_label_row():
    assert is_label_row("| Earnings per share: |  |  |")
    assert not is_label_row("| Basic | $ 8.71 | $ 8.68 |")


def test_split_table_repeats_header_and_section_label():
    md = compact_table(TABLE)
    pieces = split_table(md, limit=40, count=count)
    assert len(pieces) > 1
    for piece in pieces:
        assert piece.startswith("| Fiscal Year | 2025 | 2024 |\n| --- | --- | --- |")
    # 주식 수 Diluted 행이 든 조각에는 그 섹션 행도 같이 있어야 한다
    shares_piece = next(p for p in pieces if "| Diluted | 808 | 834 |" in p)
    assert "Weighted average number of shares:" in shares_piece
    # 조각이 섹션 행으로 끝나면 안 된다 (내용 없이 매달린 섹션 행)
    for piece in pieces:
        assert not is_label_row(piece.split("\n")[-1])


def test_small_table_is_not_split():
    assert split_table(compact_table(TABLE), limit=1000, count=count) == [compact_table(TABLE)]


def test_overlong_heading_is_kept_as_text():
    """Lam 10-K: 파서가 672자짜리 문단을 제목으로 분류했다. 제목으로 쓰면 내용이 청크에서 사라진다."""
    from chunk import MAX_HEADING, make_chunks

    long_para = "Our results depend on " + "customer investment in wafer fabrication equipment " * 12
    assert len(long_para) > MAX_HEADING
    pages = [{"page": 5, "width": 612, "height": 792, "blocks": [
        {"id": "p005_i000", "type": "heading", "bbox": [0, 0, 1, 1], "text": "# Risk Factors"},
        {"id": "p005_i001", "type": "heading", "bbox": [0, 2, 1, 3], "text": f"**{long_para}**"},
    ]}]
    doc = {"doc_id": "lrcx-10k-fy2025", "company": "Lam Research", "fiscal_year": 2025, "doc_type": "10-K"}
    chunks = make_chunks(pages, count, doc)
    assert len(chunks) == 1
    assert chunks[0]["heading"] == "Risk Factors"  # 진짜 제목은 그대로
    assert "wafer fabrication equipment" in chunks[0]["text"]  # 내용이 살아 있다