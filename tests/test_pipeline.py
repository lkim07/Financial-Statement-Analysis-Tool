"""Upload processing runner and worker (Phase 9-2). Fake steps: no LlamaParse, model or Milvus."""

import time

import pytest

import store
from pipeline import NUMBERS, NumbersNeedReview, Worker, run


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "app.db"
    with store.connect(path) as c:
        store.add_company(c, "lrcx", "Lam Research", "LRCX")
        store.add_document(c, {"doc_id": "lrcx-10k-fy2025", "company_id": "lrcx", "doc_type": "10-K",
                               "fiscal_year": 2025, "file": "x.pdf", "sha256": "a" * 64})
    return lambda: store.connect(path)


def steps(log, fail=None, review=None):
    def make(name):
        def step(doc):
            log.append((name, doc["doc_id"]))
            if name == fail:
                raise RuntimeError("boom")
            if name == review:
                raise NumbersNeedReview("gross_profit: reported 1 vs computed 2")
        return step
    return {n: make(n) for n in ("parsing", "chunking", "indexing", "extracting")}


def status(connect, doc_id="lrcx-10k-fy2025"):
    with connect() as c:
        return store.get_document(c, doc_id)


def test_all_steps_in_order_then_ready(db):
    log, ready = [], []
    assert run("lrcx-10k-fy2025", steps(log), db, on_ready=ready.append) == "ready"
    assert [n for n, _ in log] == ["parsing", "chunking", "indexing", "extracting"]
    assert status(db)["status"] == "ready" and ready == ["lrcx-10k-fy2025"]


def test_failure_records_step_and_reason(db):
    log = []
    assert run("lrcx-10k-fy2025", steps(log, fail="chunking"), db) == "failed"
    doc = status(db)
    assert doc["status"] == "failed" and doc["error"].startswith("chunking: RuntimeError: boom")
    assert [n for n, _ in log] == ["parsing", "chunking"]  # 실패한 뒤로는 진행하지 않는다


def test_number_check_failure_is_a_warning_not_a_failure(db):
    assert run("lrcx-10k-fy2025", steps([], review="extracting"), db) == "ready"
    assert status(db)["warning"].startswith(NUMBERS)


def test_review_outside_extracting_is_a_failure(db):
    assert run("lrcx-10k-fy2025", steps([], review="indexing"), db) == "failed"


def test_rerun_drops_old_number_warning_but_keeps_upload_warning(db):
    with db() as c:
        store.set_status(c, "lrcx-10k-fy2025", "uploaded", warning="company mismatch: x")
    run("lrcx-10k-fy2025", steps([], review="extracting"), db)
    assert status(db)["warning"].count(NUMBERS) == 1
    run("lrcx-10k-fy2025", steps([]), db)  # 다시 처리했더니 검산 통과
    assert status(db)["warning"] == "company mismatch: x"


def test_orphans_are_requeued_on_start(db):
    """서버가 parsing 중에 꺼졌다. 다시 켜지면 그 문서를 처음부터 처리한다."""
    with db() as c:
        store.set_status(c, "lrcx-10k-fy2025", "parsing")
    log = []
    w = Worker(steps(log), connect=db)
    assert w.start() == ["lrcx-10k-fy2025"]
    w.stop(timeout=5)
    assert status(db)["status"] == "ready" and log[0][0] == "parsing"


def test_worker_processes_submitted_jobs_one_at_a_time(db):
    with db() as c:
        store.add_document(c, {"doc_id": "lrcx-10k-fy2024", "company_id": "lrcx", "doc_type": "10-K",
                               "fiscal_year": 2024, "file": "y.pdf", "sha256": "b" * 64})
    log = []
    w = Worker(steps(log), connect=db)
    w.start()  # 두 문서 모두 uploaded: 시작할 때 대기열에 들어간다
    w.stop(timeout=5)
    docs = [d for _, d in log]
    assert docs == ["lrcx-10k-fy2025"] * 4 + ["lrcx-10k-fy2024"] * 4  # 섞이지 않는다