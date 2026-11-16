"""Document registry: everything the pipeline knows about a document comes from data/sources.json.

Adding a document = adding one entry there. No document-specific values live in code.
"""

import json
from pathlib import Path

SOURCES = Path("data/sources.json")
DATA = Path("data")


def load_sources() -> list[dict]:
    return json.loads(SOURCES.read_text(encoding="utf-8-sig"))


def get_doc(doc_id: str) -> dict:
    docs = {d["doc_id"]: d for d in load_sources()}
    if doc_id not in docs:
        raise SystemExit(f"unknown doc '{doc_id}'. registered: {', '.join(docs)}")
    return docs[doc_id]


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
