"""Chunks (jsonl) -> embeddings -> Milvus collection.

Usage: uv run --project backend python backend/ingest/index.py <chunks.jsonl> <collection_name>
Example: ... data/chunks/llamaparse.jsonl chunks_llamaparse_v2_2

컬렉션 이름에 청킹 버전을 넣는다. 버전마다 따로 저장해야 3-4에서 서로 비교할 수 있다.
"""

import json
import sys
from pathlib import Path

from pymilvus import DataType, MilvusClient
from sentence_transformers import SentenceTransformer

DB_PATH = "data/milvus.db"  # 파일 경로 = Milvus Lite. 나중에 서버 주소로 바꾸면 그대로 Milvus/Zilliz로 간다
MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384  # 이 모델의 벡터 차원


def load_chunks(path: Path) -> list[dict]:
    """jsonl: 한 줄에 청크 하나."""
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def create_collection(client: MilvusClient, name: str) -> None:
    """컬렉션(= 테이블)을 새로 만든다. 같은 이름이 있으면 지우고 다시 만든다."""
    if client.has_collection(name):
        client.drop_collection(name)

    # 스키마: 어떤 필드를 어떤 타입으로 저장할지 미리 정한다 (SQL의 CREATE TABLE과 비슷)
    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=32)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=DIM)
    schema.add_field("text", DataType.VARCHAR, max_length=16384)  # 원문. LLM과 화면에 보여줄 것
    schema.add_field("page", DataType.INT64)
    schema.add_field("section", DataType.VARCHAR, max_length=16)  # body / exhibit: 필터에 쓴다
    schema.add_field("heading", DataType.VARCHAR, max_length=512)
    schema.add_field("type", DataType.VARCHAR, max_length=16)
    schema.add_field("locations", DataType.JSON)  # [{page, bbox}, ...]: 하이라이트에 쓴다

    # 인덱스: 벡터를 어떻게 찾을지. FLAT은 모든 벡터와 하나하나 비교하는 정확한 방식이다.
    # 청크가 수백 개라 충분히 빠르다. 수백만 개가 되면 근사 인덱스(HNSW 등)를 쓴다
    index_params = client.prepare_index_params()
    index_params.add_index(field_name="vector", index_type="FLAT", metric_type="COSINE")

    client.create_collection(name, schema=schema, index_params=index_params)


def main(chunks_path: str, collection: str) -> None:
    chunks = load_chunks(Path(chunks_path))
    print(f"loaded {len(chunks)} chunks from {chunks_path}")

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

    rows = [
        {
            "chunk_id": c["chunk_id"],
            "vector": v.tolist(),
            "text": c["text"],
            "page": c["page"],
            "section": c["section"],
            "heading": c["heading"] or "",  # VARCHAR 필드는 None을 받지 않는다
            "type": c["type"],
            "locations": c["locations"],
        }
        for c, v in zip(chunks, vectors)
    ]
    client.insert(collection, rows)

    stats = client.get_collection_stats(collection)
    print(f"collection '{collection}': {stats['row_count']} rows in {DB_PATH}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])