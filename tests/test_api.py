"""API 테스트: 가짜 Deps와 가짜 ask로 엔드포인트 모양만 확인한다 (모델, Milvus, OpenAI 없이)."""
import pytest

pytest.importorskip("fastapi")  # CI에서 fastapi가 없으면 건너뛴다
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from service import Deps  # noqa: E402

REGISTRY = [{"doc_id": "amat-10k-fy2025", "doc_type": "10-K", "fiscal_year": 2025,
             "file": "amat_10k_fy2025.pdf", "company": "Applied Materials"}]


@pytest.fixture
def client(monkeypatch):
    main.STATE["deps"] = Deps(embedder=None, milvus=None, llm=None, model="m", router_model="m",
                              collection="test", registry=REGISTRY)
    monkeypatch.setattr(main, "ask", lambda question, deps, k, route, decompose: {
        "question": question, "status": "answered", "k": k, "route": {"docs": [], "decomposed": decompose}})
    with TestClient(main.app) as c:
        yield c
    main.STATE.clear()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "collection": "test", "documents": 1}


def test_list_docs(client):
    assert client.get("/docs/list").json() == [
        {"doc_id": "amat-10k-fy2025", "label": "FY2025 10-K", "doc_type": "10-K", "fiscal_year": 2025}]


def test_unknown_pdf_is_404(client):
    assert client.get("/docs/nope/pdf").status_code == 404


def test_ask_passes_options(client):
    r = client.post("/ask", json={"question": "Why did net income decrease?", "k": 8, "decompose": False})
    assert r.status_code == 200
    assert r.json()["k"] == 8 and r.json()["route"]["decomposed"] is False


def test_ask_validates_input(client):
    assert client.post("/ask", json={"question": "hi"}).status_code == 422
    assert client.post("/ask", json={"question": "Why?", "route": "magic"}).status_code == 422