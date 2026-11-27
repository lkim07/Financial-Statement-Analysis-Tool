"""Milvus filter expressions. Kept apart from answer.py so CI can test them without the embedding model."""


def build_filter(docs: list[str] | None = None, company: str | None = None) -> str:
    """기본은 본문만 (decision 014). docs를 주면 그 문서들만 (decision 019).

    company는 회사 경계다 (decision 033). 라우터(LLM일 수도 있다)가 고른 docs와 별개로 코드가 항상 건다:
    라우터가 틀려도 다른 회사의 청크는 들어오지 않는다.
    """
    expr = 'section == "body"'
    if company:
        if not company.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"bad company id: {company!r}")  # 필터 문자열에 그대로 들어가므로 막는다
        expr += f' and company_id == "{company}"'
    if docs:
        expr += " and doc_id in [" + ", ".join(f'"{d}"' for d in docs) + "]"
    return expr