"""quote와 청크 비교용 정규화: 평가와 검증이 같은 규칙을 쓴다 (decision 028)."""
from textnorm import norm


def test_markdown_table_and_pdf_text_normalize_the_same():
    pdf = "Capital expenditures $ 507 $ 57 $ 1,696 $ 2,260"
    chunk = "| Capital expenditures | $507 | $57 | $1,696 | $2,260 |"
    assert norm(pdf) == norm(chunk)


def test_percent_spacing_and_markdown_escapes():
    assert norm("Gross margin 48.7 %") == norm("**Gross margin** 48.7%")
    assert norm(r"Average price paid per share \*") == norm("Average price paid per share *")