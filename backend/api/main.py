"""FastAPI 서버 (Phase 8).

    uv run --project backend --env-file .env uvicorn main:app --app-dir backend/api --reload

엔드포인트:
    GET  /health              서버와 컬렉션 상태
    GET  /docs/list           등록된 문서 목록 (/docs는 FastAPI의 API 문서 페이지라 피했다)
    GET  /docs/{doc_id}/pdf   PDF 파일 (뷰어가 연다)
    GET  /reports/{doc_id}/summary   핵심 지표 + 순이익 워터폴 (숫자 테이블, LLM 없음)
    POST /ask                 질문 -> 답변 + 출처 + 하이라이트
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(Path(__file__).resolve().parent), str(ROOT / "rag"), str(ROOT / "ingest"), str(ROOT / "numbers")]
from service import Deps, ask, report_summary  # noqa: E402

COLLECTION = os.environ.get("COLLECTION", "docs_v2_3")
STATE: dict = {}


def build_deps() -> Deps:
    """무거운 것들(임베딩 모델, Milvus, OpenAI 클라이언트)을 한 번만 만든다."""
    from answer import DB_PATH, EMBED_MODEL, missing_docs
    from openai import OpenAI
    from pymilvus import MilvusClient
    from router import load_docs
    from sentence_transformers import SentenceTransformer

    milvus = MilvusClient(DB_PATH)
    milvus.load_collection(COLLECTION)
    registry = load_docs()
    if missing := missing_docs(milvus, COLLECTION, registry):
        raise RuntimeError(f"collection '{COLLECTION}' has no chunks for: {', '.join(missing)}")
    model = os.environ["OPENAI_MODEL"]
    return Deps(embedder=SentenceTransformer(EMBED_MODEL), milvus=milvus, llm=OpenAI(), model=model,
                router_model=os.environ.get("ROUTER_MODEL") or model, collection=COLLECTION, registry=registry)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if STATE.get("deps") is None:  # 테스트는 가짜 Deps를 미리 넣어 둔다
        STATE["deps"] = build_deps()
    yield
    for pdf in STATE["deps"].pdfs.values():
        pdf.close()


app = FastAPI(title="Evidence-Based Financial Report Analyst", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    k: int = Field(default=10, ge=1, le=20)
    route: str = Field(default="cascade", pattern="^(none|rule|cascade|llm)$")
    decompose: bool = True


@app.get("/health")
def health():
    deps = STATE["deps"]
    return {"status": "ok", "collection": deps.collection, "documents": len(deps.registry)}


@app.get("/docs/list")
def list_docs():
    from registry import doc_label
    return [{"doc_id": d["doc_id"], "label": doc_label(d), "doc_type": d["doc_type"],
             "fiscal_year": d["fiscal_year"]} for d in STATE["deps"].registry]


@app.get("/docs/{doc_id}/pdf")
def get_pdf(doc_id: str):
    from registry import pdf_path
    doc = next((d for d in STATE["deps"].registry if d["doc_id"] == doc_id), None)
    if doc is None:
        raise HTTPException(404, f"unknown document: {doc_id}")
    return FileResponse(pdf_path(doc), media_type="application/pdf")


@app.get("/reports/{doc_id}/summary")
def get_summary(doc_id: str):
    doc = next((d for d in STATE["deps"].registry if d["doc_id"] == doc_id), None)
    if doc is None:
        raise HTTPException(404, f"unknown document: {doc_id}")
    if doc["doc_type"] != "10-K":
        raise HTTPException(400, "summaries need financial statements (10-K only)")
    try:
        return report_summary(doc_id, STATE["deps"])
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/ask")
def post_ask(req: AskRequest):
    # 동기 함수(def)로 둔다: FastAPI가 스레드에서 돌려서, LLM을 기다리는 동안 서버가 멈추지 않는다
    return ask(req.question, STATE["deps"], k=req.k, route=req.route, decompose=req.decompose)