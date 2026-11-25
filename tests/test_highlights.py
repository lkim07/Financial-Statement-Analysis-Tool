"""하이라이트 좌표 테스트. 순수 함수는 그대로, PDF가 필요한 locate()는 테스트 안에서 작은 PDF를 만든다."""
import pytest
from highlights import block_highlights, computed_terms, row_band, search_terms


def test_search_terms_keep_pdf_formatting_and_skip_years_and_forms():
    s = "Per the FY2025 10-K, the tax provision rose from $975 million to $2,273 million (24.5% rate) in 2025."
    assert search_terms(s) == ["975", "2,273", "24.5"]


def test_search_terms_skip_small_integers():
    assert search_terms("Revenue grew in 4 regions and 2 segments.") == []


def test_computed_terms_take_reported_values_not_contributions():
    text = ("Net income bridge, fiscal 2024 -> 2025 (USD millions)\n"  # 연도는 찾지 않는다 (표 머리글이 칠해졌다)
            "Net income: 7,177 -> 6,998 (change -179)\n"
            "- Provision for income taxes: 975 -> 2,273, contribution -1,298\n"
            "Diluted EPS: 8.61 -> 8.66 (reported change +0.05).")
    assert computed_terms(text) == ["7,177", "6,998", "975", "2,273", "8.61", "8.66"]


def test_row_band_spans_the_whole_table_row_inside_the_block():
    # 손익계산서의 한 행과 그 위아래 행 (x0, y0, x1, y1, text)
    words = [(17, 246, 80, 256, "Income"), (480, 246, 510, 256, "9,271"),
             (17, 258, 114, 268, "Provision"), (420, 258, 450, 268, "2,273"), (500, 258, 520, 268, "975"),
             (17, 270, 60, 280, "Net"), (480, 270, 510, 280, "6,998"),
             (17, 700, 60, 710, "footer")]
    block = [10, 240, 580, 290]
    assert row_band([420, 258, 450, 268], words, block) == [17, 258, 520, 268]


def test_row_band_ignores_words_outside_the_block():
    words = [(17, 258, 114, 268, "Provision"), (420, 258, 450, 268, "2,273"), (600, 258, 650, 268, "sidebar")]
    assert row_band([420, 258, 450, 268], words, [10, 240, 580, 290]) == [17, 258, 450, 268]


def test_block_highlights_keep_pages():
    locs = [{"page": 22, "bbox": [36.62, 371.6, 460.9, 381.5]}, {"page": 23, "bbox": [32.6, 50.2, 294.6, 60.2]}]
    assert block_highlights(locs) == [{"page": 22, "bbox": [36.6, 371.6, 460.9, 381.5], "level": "block"},
                                      {"page": 23, "bbox": [32.6, 50.2, 294.6, 60.2], "level": "block"}]


def test_locate_on_a_real_pdf_page():
    pymupdf = pytest.importorskip("pymupdf")  # CI에는 PyMuPDF가 없다
    from highlights import locate

    pdf = pymupdf.open()
    page = pdf.new_page(width=600, height=800)
    page.insert_text((20, 100), "Provision for income taxes")
    page.insert_text((400, 100), "2,273")
    page.insert_text((480, 100), "975")
    page.insert_text((400, 500), "2,273")  # 같은 숫자가 블록 밖에도 있다
    block = {"page": 1, "bbox": [10, 80, 580, 120]}

    hits = locate(pdf, [block], ["2,273"])
    exact = [h for h in hits if h["level"] == "exact"]
    assert [h["level"] for h in hits].count("block") == 1
    assert len(exact) == 1                                   # 블록 밖의 2,273은 칠하지 않는다
    x0, y0, x1, y1 = exact[0]["bbox"]
    assert x0 < 30 and x1 > 490 and 85 < y1 < 110            # 라벨부터 975까지, 그 행 하나