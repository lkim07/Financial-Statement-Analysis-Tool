"""Question -> retrieve chunks -> LLM answer where every sentence cites its chunks.

Usage:
    uv run --project backend --env-file .env python backend/rag/answer.py "Why did net income decrease?"
    ... --k 5 --collection chunks_llamaparse_v2_2

.env needs OPENAI_API_KEY and OPENAI_MODEL.
"""

import argparse
import os
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field
from pymilvus import MilvusClient
from sentence_transformers import SentenceTransformer

DB_PATH = "data/milvus.db"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"


# ---------- 1. 출력 형식: LLM이 반드시 이 모양으로 답하게 한다 ----------

class Sentence(BaseModel):
    text: str = Field(description="One sentence of the answer.")
    citations: list[str] = Field(description="IDs of the context chunks that support this sentence, e.g. ['c0123'].")
    inferred: bool = Field(description="True if the sentence is your own inference or calculation, not stated in the chunks.")


class Answer(BaseModel):
    status: Literal["answered", "partial", "no_evidence"] = Field(
        description="answered: the context fully answers the question. "
        "partial: the context answers part of it (e.g. what changed but not why). "
        "no_evidence: the context contains nothing relevant."
    )
    sentences: list[Sentence]
    missing: str = Field(description="For partial or no_evidence: what information is missing. Empty if answered.")


INSTRUCTIONS = """You answer questions about a company's annual report using ONLY the context chunks provided.

Rules:
- Every sentence must cite at least one chunk ID from the context, in `citations`.
- Copy numbers exactly as they appear in the context. Do not use outside knowledge.
- If a sentence is your own inference or calculation rather than something the report states, set `inferred` to true.
- If the context fully answers the question, set `status` to "answered".
- If it answers only part of the question (for example, it shows WHAT changed but not WHY),
  set `status` to "partial", answer the supported part, and describe what is missing in `missing`.
  Never fill the gap with a plausible guess.
- If the context contains nothing relevant, set `status` to "no_evidence" and return no sentences.
- Be concise: at most 6 sentences."""


# ---------- 2. 검색 ----------

def retrieve(question: str, collection: str, k: int, embedder, milvus) -> list[dict]:
    vec = embedder.encode([question], normalize_embeddings=True)
    hits = milvus.search(
        collection,
        data=vec.tolist(),
        limit=k,
        output_fields=["text", "page", "heading", "locations"],
    )[0]
    return [{"chunk_id": h.get("id", h.get("chunk_id")), **h["entity"]} for h in hits]


def build_context(chunks: list[dict]) -> str:
    """청크마다 ID와 출처를 머리에 붙인다. LLM은 이 ID로 인용한다."""
    parts = [f"[{c['chunk_id']}] (page {c['page']}, {c['heading']})\n{c['text']}" for c in chunks]
    return "\n\n---\n\n".join(parts)


# ---------- 3. 생성 ----------

def generate(question: str, chunks: list[dict], llm: OpenAI, model: str) -> Answer:
    response = llm.responses.parse(
        model=model,
        instructions=INSTRUCTIONS,
        input=f"Context:\n\n{build_context(chunks)}\n\nQuestion: {question}",
        text_format=Answer,  # Pydantic 모델을 넘기면 SDK가 JSON 스키마로 바꿔 형식을 강제한다
    )
    return response.output_parsed


# ---------- 4. 검증: LLM을 믿지 않고 코드로 확인한다 ----------

def validate(answer: Answer, chunks: list[dict]) -> list[str]:
    """인용이 실제로 받은 청크를 가리키는지 확인한다. 문제 목록을 돌려준다."""
    known = {c["chunk_id"] for c in chunks}
    problems = []
    for i, s in enumerate(answer.sentences, start=1):
        if not s.citations:
            problems.append(f"sentence {i}: no citation")
        for cid in s.citations:
            if cid not in known:
                problems.append(f"sentence {i}: cites unknown chunk {cid!r}")
    if answer.status == "no_evidence" and answer.sentences:
        problems.append("status=no_evidence but sentences were returned")
    if answer.status == "partial" and not answer.missing:
        problems.append("status=partial but nothing listed as missing")
    return problems


def show(answer: Answer, chunks: list[dict], problems: list[str]) -> None:
    pages = {c["chunk_id"]: c["page"] for c in chunks}
    print(f"[{answer.status}]")
    for s in answer.sentences:
        refs = ", ".join(f"{cid} p{pages.get(cid, '?')}" for cid in s.citations)
        tag = " (inferred)" if s.inferred else ""
        print(f"- {s.text}{tag}  [{refs}]")
    if answer.missing:
        print(f"missing: {answer.missing}")
    print("\nretrieved:", ", ".join(f"{c['chunk_id']} p{c['page']}" for c in chunks))
    print("citation check:", "OK" if not problems else problems)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--collection", default="chunks_llamaparse_v2_2")
    args = parser.parse_args()

    embedder = SentenceTransformer(EMBED_MODEL)
    milvus = MilvusClient(DB_PATH)
    milvus.load_collection(args.collection)
    llm = OpenAI()  # OPENAI_API_KEY 환경 변수를 읽는다

    chunks = retrieve(args.question, args.collection, args.k, embedder, milvus)
    answer = generate(args.question, chunks, llm, os.environ["OPENAI_MODEL"])
    show(answer, chunks, validate(answer, chunks))


if __name__ == "__main__":
    main()