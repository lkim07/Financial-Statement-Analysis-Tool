"""Chunks (jsonl) -> embeddings -> Milvus collection.

Usage: uv run --project backend python backend/ingest/index.py --collection <name> --chunks <file.jsonl> [<file.jsonl> ...]
Example: ... --collection docs_v2_2 --chunks data/chunks/amat-10k-fy2025/llamaparse.jsonl data/chunks/amat-10k-fy2024/llamaparse.jsonl

여러 문서를 한 컬렉션에 넣고, 문서는 메타데이터(doc_id, fiscal_year)로 구분한다 (decision 019).
컬렉션 이름에는 청킹 버전이나 문서 구성을 넣어서, 서로 다른 구성을 나란히 비교할 수 있게 한다.
"""

import argparse
import json
from pathlib import Path

from pymilvus import DataType, MilvusClient
from sentence_transformers import SentenceTransformer

from registry import get_doc

DB_PATH = "data/milvus.db"  # 파일 경로 = Milvus Lite. 나중에 서버 주소로 바꾸면 그대로 Milvus/Zilliz로 간다
MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384  # 이 모델의 벡터 차원


def load_chunks(path: Path) -> list[dict]:
    """jsonl: 한 줄에 청크 하나."""
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def create_collection(client: MilvusClient, name: str) -> None:
    """CLI용: 컬렉션(= 테이블)을 처음부터 다시 만든다. 같은 이름이 있으면 지운다.

    업로드 파이프라인은 이 함수를 쓰면 안 된다: 다른 문서들의 청크까지 사라진다 (ensure_collection을 쓴다).
    """
    if client.has_collection(name):
        client.drop_collection(name)
    ensure_collection(client, name)


def ensure_collection(client: MilvusClient, name: str) -> None:
    """없을 때만 만든다."""
    if client.has_collection(name):
        return

    # 스키마: 어떤 필드를 어떤 타입으로 저장할지 미리 정한다 (SQL의 CREATE TABLE과 비슷)
    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)  # 'amat-10k-fy2025:c0123'
    schema.add_field("company_id", DataType.VARCHAR, max_length=32)  # 필터: 회사 경계 (decision 033)
    schema.add_field("doc_id", DataType.VARCHAR, max_length=64)  # 필터: 특정 문서만
    schema.add_field("doc_label", DataType.VARCHAR, max_length=32)  # 인용 표시: 'FY2025 10-K'
    schema.add_field("doc_type", DataType.VARCHAR, max_length=16)  # 필터: 10-K / DEF 14A / 8-K
    schema.add_field("fiscal_year", DataType.INT64)  # 필터: 특정 연도만
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=DIM)
    schema.add_field("text", DataType.VARCHAR, max_length=TEXT_BYTES)  # 원문. LLM과 화면에 보여줄 것
    schema.add_field("page", DataType.INT64)
    schema.add_field("section", DataType.VARCHAR, max_length=16)  # body / exhibit: 필터에 쓴다
    schema.add_field("heading", DataType.VARCHAR, max_length=HEADING_BYTES)
    schema.add_field("type", DataType.VARCHAR, max_length=16)
    schema.add_field("locations", DataType.JSON)  # [{page, bbox}, ...]: 하이라이트에 쓴다

    # 인덱스: 벡터를 어떻게 찾을지. FLAT은 모든 벡터와 하나하나 비교하는 정확한 방식이다.
    # 청크가 수백 개라 충분히 빠르다. 수백만 개가 되면 근사 인덱스(HNSW 등)를 쓴다
    index_params = client.prepare_index_params()
    index_params.add_index(field_name="vector", index_type="FLAT", metric_type="COSINE")

    client.create_collection(name, schema=schema, index_params=index_params)


def main(collection: str, chunk_files: list[Path]) -> None:
    chunks = []
    for path in chunk_files:
        part = load_chunks(path)
        print(f"loaded {len(part)} chunks from {path}")
        chunks += part

    # 임베딩은 embed_text(맥락 머리글 포함)로 만든다. 저장하는 text는 원문이다
    model = SentenceTransformer(MODEL_NAME)
    vectors = model.encode(
        [c["embed_text"] for c in chunks],
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=True,
    )

    client = MilvusClient(DB_PATH)
    create_collection(client, collection)

    client.insert(collection, build_rows(chunks, vectors))

    stats = client.get_collection_stats(collection)
    print(f"collection '{collection}': {stats['row_count']} rows in {DB_PATH}")


HEADING_BYTES, TEXT_BYTES = 512, 16384  # 스키마의 max_length와 같게


def fit(value: str, limit: int) -> str:
    """Milvus VARCHAR의 max_length는 바이트 단위다. UTF-8 글자가 중간에서 잘리지 않게 자른다."""
    data = value.encode("utf-8")
    return value if len(data) <= limit else data[:limit].decode("utf-8", errors="ignore")


def build_rows(chunks: list[dict], vectors) -> list[dict]:
    # 회사는 청크 파일이 아니라 레지스트리에서 가져온다: 사실의 출처는 한 곳이어야 한다.
    # 그래서 예전에 만든 청크 파일도 다시 청킹하지 않고 그대로 쓸 수 있다
    company_of = {d: get_doc(d)["company_id"] for d in {c["doc_id"] for c in chunks}}
    for c in chunks:  # 본문은 자르면 근거가 사라진다: 조용히 자르지 말고 멈춘다
        if len(c["text"].encode("utf-8")) > TEXT_BYTES:
            raise ValueError(f"{c['chunk_id']}: text is {len(c['text'].encode('utf-8'))} bytes > {TEXT_BYTES}")
    return [
        {
            "chunk_id": c["chunk_id"],
            "company_id": company_of[c["doc_id"]],
            "doc_id": c["doc_id"],
            "doc_label": c["doc_label"],
            "doc_type": c["doc_type"],
            "fiscal_year": c["fiscal_year"],
            "vector": v.tolist(),
            "text": c["text"],
            "page": c["page"],
            "section": c["section"],
            # VARCHAR 필드는 None을 받지 않는다. 제목은 표시용이라 길면 잘라도 된다
            "heading": fit(c["heading"] or "", HEADING_BYTES),
            "type": c["type"],
            "locations": c["locations"],
        }
        for c, v in zip(chunks, vectors)
    ]


def replace_document(client: MilvusClient, collection: str, doc_id: str, chunks: list[dict], model) -> int:
    """업로드 파이프라인용: 이 문서의 청크를 지우고 다시 넣는다.

    Milvus는 같은 chunk_id를 insert해도 오류 없이 중복으로 넣는다. 그래서 다시 처리할 때는 먼저 지운다.
    upsert만 쓰면 다시 파싱해서 청크 수가 줄었을 때 옛 청크가 남는다. 몇 번 돌려도 결과가 같다 (멱등성).
    """
    if any(c["doc_id"] != doc_id for c in chunks):
        raise ValueError("chunks from another document")
    ensure_collection(client, collection)
    vectors = model.encode([c["embed_text"] for c in chunks], batch_size=32, normalize_embeddings=True)
    client.delete(collection, filter=f'doc_id == "{doc_id}"')
    client.insert(collection, build_rows(chunks, vectors))
    return len(chunks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chunks -> Milvus collection")
    parser.add_argument("--collection", required=True)
    parser.add_argument("--chunks", required=True, nargs="+", type=Path)
    args = parser.parse_args()
    main(args.collection, args.chunks)
