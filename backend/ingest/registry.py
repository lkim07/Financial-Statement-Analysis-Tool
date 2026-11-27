"""Document registry: everything the pipeline knows about a document comes from the registry database.

Since Phase 9 the registry lives in SQLite (store.py, decision 033). data/sources.json is the committed
seed: an empty database is filled from it on first use. No document-specific values live in code.
"""

from contextlib import closing
from pathlib import Path

import store

DATA = Path("data")


def _conn():
    conn = store.connect()
    if not conn.execute("SELECT 1 FROM documents LIMIT 1").fetchone():
        store.seed(conn, store.load_seed())  # 처음 쓰는 환경: sources.json으로 채운다
    return conn


def load_sources(company_id: str | None = None) -> list[dict]:
    """질문과 대시보드에 쓸 문서: 처리를 마친(ready) 것만. company_id를 주면 그 회사 것만."""
    with closing(_conn()) as conn:  # with conn은 트랜잭션일 뿐 연결을 닫지 않는다
        return store.list_documents(conn, company_id=company_id, status="ready")


def get_doc(doc_id: str) -> dict:
    """처리 중인 문서도 찾는다: 파싱과 청킹 단계가 ready 전에 이 함수를 부른다."""
    with closing(_conn()) as conn:
        doc = store.get_document(conn, doc_id)
        if doc is None:
            known = [d["doc_id"] for d in store.list_documents(conn, status=None)]
            raise SystemExit(f"unknown doc '{doc_id}'. registered: {', '.join(known)}")
        return doc


def doc_label(doc: dict) -> str:
    """사람이 읽는 짧은 이름. 인용에 붙는다: 'FY2025 10-K'"""
    return f"FY{doc['fiscal_year']} {doc['doc_type']}"


def doc_title(doc: dict) -> str:
    """맥락 머리글에 쓰는 긴 이름: 'Applied Materials FY2025 10-K'"""
    return f"{doc['company']} {doc_label(doc)}"


def pdf_path(doc: dict) -> Path:
    return DATA / doc["file"]


def parsed_dir(doc: dict, parser: str) -> Path:
    return DATA / "parsed" / doc["doc_id"] / parser


def chunks_path(doc: dict, parser: str, strategy: str = "v2") -> Path:
    suffix = "" if strategy == "v2" else f"_{strategy}"
    return DATA / "chunks" / doc["doc_id"] / f"{parser}{suffix}.jsonl"
