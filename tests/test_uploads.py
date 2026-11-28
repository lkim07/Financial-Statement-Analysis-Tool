"""Upload API (Phase 9-2): folders, upload validation, duplicates, status, corrections, feedback.

Generated PDFs, a temporary database and a fake worker: no LlamaParse, model or Milvus.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")  # FastAPI가 파일 업로드(폼) 처리에 쓴다
pymupdf = pytest.importorskip("pymupdf")
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import store  # noqa: E402
import uploads  # noqa: E402
from service import Deps  # noqa: E402


def cover_pdf(form="Form 10-K", fy_line="For the fiscal year ended June 29, 2025", name="LAM RESEARCH CORPORATION",
              extra=""):
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in [form, fy_line, name, "(Exact name of registrant as specified in its charter)", extra]:
        page.insert_text((72, y), line)
        y += 20
    data = doc.tobytes()
    doc.close()
    return data


class FakeWorker:
    def __init__(self):
        self.jobs = []

    def submit(self, doc_id):
        self.jobs.append(doc_id)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(uploads, "DATA", tmp_path)
    main.STATE["deps"] = Deps(embedder=None, milvus=None, llm=None, model="m", router_model="m",
                              collection="test", registry=[])
    uploads.WORKER["worker"] = FakeWorker()
    with TestClient(main.app) as c:
        c.post("/companies", json={"company_id": "lrcx", "name": "Lam Research", "ticker": "LRCX"})
        yield c
    main.STATE.clear()
    uploads.WORKER["worker"] = None


def upload(client, data, company="lrcx", **form):
    return client.post(f"/companies/{company}/documents", files={"file": ("x.pdf", data, "application/pdf")},
                       data={k: str(v) for k, v in form.items()})


def test_company_folder_rules(client):
    assert client.post("/companies", json={"company_id": "lrcx", "name": "Lam"}).status_code == 409
    assert client.post("/companies", json={"company_id": "Bad Id!", "name": "x y"}).status_code == 422
    assert client.get("/companies").json()[0]["company_id"] == "lrcx"


def test_upload_reads_cover_and_queues(client, tmp_path):
    r = upload(client, cover_pdf())
    assert r.status_code == 202
    doc = r.json()["document"]
    assert doc["doc_id"] == "lrcx-10k-fy2025" and doc["status"] == "uploaded" and doc["period_end"] == "2025-06-29"
    assert doc["warning"] is None
    assert (tmp_path / "uploads" / "lrcx" / "lrcx-10k-fy2025.pdf").exists()
    assert uploads.WORKER["worker"].jobs == ["lrcx-10k-fy2025"]
    assert list((tmp_path / "uploads" / "_tmp").iterdir()) == []  # 임시 파일이 남지 않는다


def test_same_file_twice_is_not_processed_twice(client):
    data = cover_pdf()
    upload(client, data)
    r = upload(client, data)
    assert r.status_code == 200 and r.json()["created"] is False
    assert uploads.WORKER["worker"].jobs == ["lrcx-10k-fy2025"]


def test_different_file_same_year_is_a_conflict(client):
    upload(client, cover_pdf())
    assert upload(client, cover_pdf(extra="a different version")).status_code == 409


def test_wrong_company_cover_is_a_warning(client):
    r = upload(client, cover_pdf(name="Applied Materials, Inc."))
    assert r.status_code == 202 and "Applied Materials" in r.json()["document"]["warning"]


def test_rejects_non_pdf_proxy_unknown_folder(client):
    assert upload(client, b"hello, not a pdf").status_code == 400
    r = upload(client, cover_pdf(form="SCHEDULE 14A", fy_line=""))
    assert r.status_code == 400 and "DEF 14A" in r.json()["detail"]
    assert upload(client, cover_pdf(), company="nope").status_code == 404


def test_missing_fiscal_year_can_be_given_by_the_user(client):
    data = cover_pdf(fy_line="Annual report")
    r = upload(client, data)
    assert r.status_code == 422 and "fiscal_year" in r.json()["detail"]["detected"]["missing"]
    r = upload(client, data, fiscal_year=2025)
    assert r.status_code == 202 and r.json()["document"]["doc_id"] == "lrcx-10k-fy2025"


def test_too_large(client, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_BYTES", 100)
    assert upload(client, cover_pdf()).status_code == 413


def test_correction_is_saved_as_feedback_and_reprocessed(client):
    upload(client, cover_pdf())
    with store.connect() as c:
        store.set_status(c, "lrcx-10k-fy2025", "ready")
    r = client.patch("/documents/lrcx-10k-fy2025", json={"period_end": "2025-06-28", "note": "typo on cover"})
    assert r.status_code == 202
    assert client.get("/documents/lrcx-10k-fy2025").json()["period_end"] == "2025-06-28"
    assert uploads.WORKER["worker"].jobs == ["lrcx-10k-fy2025", "lrcx-10k-fy2025"]
    with store.connect() as c:
        fb = dict(c.execute("SELECT * FROM feedback").fetchone())
    assert fb["kind"] == "metadata" and '"2025-06-29"' in fb["context"]


def test_correction_waits_for_processing_and_retry_needs_failure(client):
    upload(client, cover_pdf())
    assert client.patch("/documents/lrcx-10k-fy2025", json={"fiscal_year": 2024}).status_code == 409
    assert client.post("/documents/lrcx-10k-fy2025/retry").status_code == 409
    with store.connect() as c:
        store.set_status(c, "lrcx-10k-fy2025", "failed", error="parsing: timeout")
    assert client.post("/documents/lrcx-10k-fy2025/retry").status_code == 202


def test_feedback(client):
    r = client.post("/feedback", json={"kind": "answer", "message": "wrong total", "context": {"q": "why"}})
    assert r.status_code == 201
    assert client.post("/feedback", json={"kind": "praise", "message": "nice"}).status_code == 422
    assert client.post("/feedback", json={"kind": "number", "message": "x y z", "doc_id": "nope"}).status_code == 404