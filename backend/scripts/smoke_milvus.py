"""Milvus Lite + 임베딩 모델이 이 컴퓨터에서 돌아가는지 확인한다."""
from pymilvus import MilvusClient
from sentence_transformers import SentenceTransformer

docs = [
    "Net revenue increased 4% in fiscal 2025.",
    "The effective tax rate rose from 12.0% to 24.5%.",
    "We approved a workforce reduction plan affecting about 4% of employees.",
]

model = SentenceTransformer("BAAI/bge-small-en-v1.5")  # 처음 실행 때 모델을 내려받는다
vectors = model.encode(docs, normalize_embeddings=True)
print("vector shape:", vectors.shape)  # (문장 수, 차원 수)

client = MilvusClient("data/smoke.db")  # 파일 경로를 주면 Milvus Lite로 동작한다
if client.has_collection("smoke"):
    client.drop_collection("smoke")
client.create_collection("smoke", dimension=vectors.shape[1], metric_type="COSINE")
client.insert("smoke", [
    {"id": i, "vector": v.tolist(), "text": t}
    for i, (v, t) in enumerate(zip(vectors, docs))
])

# query = model.encode(["Why did taxes go up?"], normalize_embeddings=True)
query = model.encode(
    ["Represent this sentence for searching relevant passages: Why did taxes go up?"],
    normalize_embeddings=True,
)
hits = client.search("smoke", data=query.tolist(), limit=2, output_fields=["text"])
for hit in hits[0]:
    print(round(hit["distance"], 3), hit["entity"]["text"])