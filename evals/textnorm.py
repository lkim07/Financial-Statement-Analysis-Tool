"""quote와 청크를 비교할 때 쓰는 정규화. eval_retrieval.py와 validate.py가 같은 규칙을 써야 한다.

(validate.py가 다른 규칙으로 확인하면 "검증은 통과했는데 평가에서는 못 찾는" 일이 생긴다.)
"""
import re


def norm(text: str) -> str:
    """두 파서의 표기 차이를 지운다: 마크다운/HTML 기호, 표의 |, 특수 공백, '7 %' vs '7%', '$ 507' vs '$507'."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*#|]", " ", text)
    text = text.replace("\\", " ")  # 마크다운 이스케이프(\*) 제거
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+%", "%", text)
    text = re.sub(r"\$\s+", "$", text)  # "$ 507"(PyMuPDF) vs "$507"(LlamaParse 일부 표): 같은 숫자다
    return re.sub(r"\s+", " ", text).strip().lower()