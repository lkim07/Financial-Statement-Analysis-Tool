"""SQLite registry (Phase 9-1, decision 033). No PDFs, Milvus or OpenAI needed."""

import json
import re
import sqlite3
import threading
from pathlib import Path

import pytest

import store
from filters import build_filter

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "data" / "sources.json").read_text(encoding="utf-8-sig"))


def lam(**over):
    doc = {"doc_id": "lrcx-10k-fy2025", "company_id": "lrcx", "doc_type": "10-K", "fiscal_year": 2025,
           "period_end": "2025-06-29", "file": "lrcx_10k_fy2025.pdf", "sha256": "b" * 64}
    return {**doc, **over}


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "app.db")
    yield c
    c.close()


def test_seed_shapes_like_sources_json_and_is_idempotent(conn):
    assert store.seed(conn, SEED) == len(SEED)
    assert store.seed(conn, SEED) == 0  # 두 번 돌려도 늘지 않는다
    docs = store.list_documents(conn)
    assert {d["doc_id"] for d in docs} == {s["doc_id"] for s in SEED}
    amat = store.get_document(conn, "amat-10k-fy2025")
    # 예전 sources.json을 읽던 코드가 쓰는 키가 그대로 있다
    for key in ("doc_id", "company", "doc_type", "fiscal_year", "file", "exhibit_start_page"):
        assert key in amat
    assert amat["company_id"] == "amat" and amat["status"] == "ready"


def test_same_file_same_company_returns_existing(conn):
    store.add_company(conn, "lrcx", "Lam Research", "LRCX")
    first, created = store.add_document(conn, lam())
    again, created_again = store.add_document(conn, lam(doc_id="lrcx-10k-fy2025-copy"))
    assert created and not created_again
    assert again["doc_id"] == first["doc_id"]  # 파싱을 다시 하지 않는다


def test_same_file_other_company_is_a_new_document(conn):
    store.add_company(conn, "lrcx", "Lam Research", "LRCX")
    store.add_company(conn, "semis", "Semis watchlist")
    store.add_document(conn, lam())
    _, created = store.add_document(conn, lam(doc_id="semis-10k-fy2025", company_id="semis"))
    assert created


def test_doc_id_clash_with_a_different_file_is_an_error(conn):
    store.add_company(conn, "lrcx", "Lam Research", "LRCX")
    store.add_document(conn, lam())
    with pytest.raises(sqlite3.IntegrityError):
        store.add_document(conn, lam(sha256="c" * 64))  # 같은 doc_id, 다른 파일: 조용히 넘기면 안 된다


def test_unknown_company_is_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):  # foreign key
        store.add_document(conn, lam(company_id="nope"))


def test_status_flow_and_ready_filter(conn):
    store.add_company(conn, "lrcx", "Lam Research", "LRCX")
    store.add_document(conn, lam())
    assert store.list_documents(conn) == []  # uploaded는 아직 질문에 쓰지 않는다
    store.set_status(conn, "lrcx-10k-fy2025", "parsing")
    store.set_status(conn, "lrcx-10k-fy2025", "ready")
    assert [d["doc_id"] for d in store.list_documents(conn, company_id="lrcx")] == ["lrcx-10k-fy2025"]
    with pytest.raises(ValueError):
        store.set_status(conn, "lrcx-10k-fy2025", "done")
    with pytest.raises(KeyError):
        store.set_status(conn, "missing", "ready")


def test_failed_keeps_the_reason(conn):
    store.add_company(conn, "lrcx", "Lam Research", "LRCX")
    store.add_document(conn, lam())
    store.set_status(conn, "lrcx-10k-fy2025", "failed", error="income statement check failed")
    assert store.get_document(conn, "lrcx-10k-fy2025")["error"] == "income statement check failed"


def test_concurrent_uploads_lose_nothing(tmp_path):
    """JSON 파일이었다면 갱신 손실(lost update)이 나는 상황: 여러 업로드가 동시에 끝난다."""
    path = tmp_path / "app.db"
    with store.connect(path) as c:
        store.add_company(c, "lrcx", "Lam Research", "LRCX")

    def upload(i):
        c = store.connect(path)  # 스레드마다 자기 연결
        store.add_document(c, lam(doc_id=f"lrcx-doc-{i}", sha256=f"{i:064x}"))
        c.close()

    threads = [threading.Thread(target=upload, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with store.connect(path) as c:
        assert len(store.list_documents(c, status=None)) == 20


def test_seed_hashes_are_filled():
    """seed의 sha256이 비어 있으면 UNIQUE(company_id, sha256)가 중복을 못 잡는다."""
    for s in SEED:
        assert re.fullmatch(r"[0-9a-f]{64}", s["sha256"]), f"{s['doc_id']}: sha256 missing or not lowercase hex"


def test_company_filter_is_always_added():
    assert build_filter(["lrcx-10k-fy2025"], "lrcx") == (
        'section == "body" and company_id == "lrcx" and doc_id in ["lrcx-10k-fy2025"]')
    assert build_filter(None, "amat") == 'section == "body" and company_id == "amat"'
    with pytest.raises(ValueError):
        build_filter(None, 'amat" or company_id != "x')  # 필터 문자열 주입