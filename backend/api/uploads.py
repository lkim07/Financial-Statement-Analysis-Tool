"""Company folders, PDF upload, processing status, corrections and feedback (Phase 9-2, decision 034).

    GET   /companies                          folders, with document counts by status
    POST  /companies                          make a folder {company_id, name, ticker?}
    GET   /companies/{id}/documents           every document in the folder, any status
    POST  /companies/{id}/documents           upload a 10-K PDF -> 202 (processing) or 200 (already there)
    GET   /documents/{doc_id}                 status, error, warning
    PATCH /documents/{doc_id}                 fix what the cover page detection got wrong -> process again
    POST  /documents/{doc_id}/retry           process a failed (or finished) document again
    POST  /feedback                           "this is wrong" reports from the user
"""

import hashlib
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import store
from detect import cover_text, detect_cover, same_company

DATA = Path("data")
MAX_BYTES = 50 * 1024 * 1024  # 50 MB: 10-K는 보통 1~5 MB다
MAX_PAGES = 400
COMPANY_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")  # 필터 문자열과 파일 경로에 들어가므로 좁게 허용한다

MISMATCH = "company mismatch: "
WORKER: dict = {"worker": None}  # main.py가 서버를 켤 때 넣는다. 테스트는 가짜를 넣는다
router = APIRouter()


def db():
    return closing(store.connect())


def worker():
    if WORKER["worker"] is None:
        raise HTTPException(503, "upload worker is not running")
    return WORKER["worker"]


# --- 회사 폴더 ---------------------------------------------------------------------------

class CompanyIn(BaseModel):
    company_id: str = Field(pattern=COMPANY_ID.pattern)
    name: str = Field(min_length=2, max_length=100)
    ticker: str | None = Field(default=None, max_length=10)


@router.get("/companies")
def list_companies():
    with db() as conn:
        counts = {}
        for r in conn.execute("SELECT company_id, status, COUNT(*) n FROM documents GROUP BY company_id, status"):
            counts.setdefault(r["company_id"], {})[r["status"]] = r["n"]
        return [{**c, "documents": counts.get(c["company_id"], {})} for c in store.list_companies(conn)]


@router.post("/companies", status_code=201)
def create_company(body: CompanyIn):
    with db() as conn:
        if store.get_company(conn, body.company_id):
            raise HTTPException(409, f"folder '{body.company_id}' already exists")
        store.add_company(conn, body.company_id, body.name, body.ticker)
        return store.get_company(conn, body.company_id)


@router.get("/companies/{company_id}/documents")
def company_documents(company_id: str):
    with db() as conn:
        if not store.get_company(conn, company_id):
            raise HTTPException(404, f"unknown folder: {company_id}")
        return store.list_documents(conn, company_id=company_id, status=None)


# --- 업로드 ------------------------------------------------------------------------------

def save_upload(file: UploadFile) -> tuple[Path, str]:
    """크기를 세면서 임시 파일로 받는다. sha256도 같이 계산한다. 다 읽은 뒤에 크기를 보면 늦다."""
    tmp_dir = DATA / "uploads" / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    with tempfile.NamedTemporaryFile(dir=tmp_dir, suffix=".pdf", delete=False) as out:
        while block := file.file.read(1024 * 1024):
            size += len(block)
            if size > MAX_BYTES:
                out.close()
                Path(out.name).unlink(missing_ok=True)
                raise HTTPException(413, f"file is larger than {MAX_BYTES // (1024 * 1024)} MB")
            digest.update(block)
            out.write(block)
    return Path(out.name), digest.hexdigest()


def read_cover(path: Path) -> dict:
    import pymupdf

    if path.read_bytes()[:5] != b"%PDF-":  # 확장자가 아니라 내용으로 판단한다
        raise HTTPException(400, "not a PDF file")
    try:
        with pymupdf.open(path) as pdf:
            if pdf.page_count > MAX_PAGES:
                raise HTTPException(400, f"too many pages ({pdf.page_count} > {MAX_PAGES})")
            return detect_cover(cover_text(pdf))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - 깨진 PDF
        raise HTTPException(400, f"could not open the PDF: {e}") from e


@router.post("/companies/{company_id}/documents", status_code=202)
def upload_document(company_id: str, file: UploadFile = File(...),
                    fiscal_year: int | None = Form(default=None, ge=1990, le=2100),
                    period_end: str | None = Form(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """표지에서 회사·문서 종류·회계연도를 읽는다. 폼으로 보낸 값이 있으면 그 값을 쓴다 (읽기 실패 대비)."""
    with db() as conn:
        company = store.get_company(conn, company_id)
    if not company:
        raise HTTPException(404, f"unknown folder: {company_id}")

    tmp, sha = save_upload(file)
    try:
        cover = read_cover(tmp)
        if fiscal_year:
            cover["fiscal_year"] = fiscal_year
        if period_end:
            cover["period_end"] = period_end
        if cover["doc_type"] != "10-K":
            raise HTTPException(400, f"only annual reports (Form 10-K) can be uploaded for now; "
                                     f"the cover page says: {cover['doc_type'] or 'unknown form'}")
        if not cover["fiscal_year"]:
            raise HTTPException(422, {"message": "could not read the fiscal year from the cover page; "
                                                 "send fiscal_year as a form field", "detected": cover})

        doc_id = f"{company_id}-10k-fy{cover['fiscal_year']}"
        doc = {"doc_id": doc_id, "company_id": company_id, "doc_type": "10-K",
               "fiscal_year": cover["fiscal_year"], "period_end": cover["period_end"],
               "file": f"uploads/{company_id}/{doc_id}.pdf", "sha256": sha}
        with db() as conn:
            try:
                saved, created = store.add_document(conn, doc)
            except sqlite3.IntegrityError as e:
                raise HTTPException(409, f"this folder already has a different FY{cover['fiscal_year']} 10-K "
                                         f"({doc_id}). Delete it first or correct the fiscal year.") from e
            if not created:  # 같은 폴더에 같은 파일: 다시 처리하지 않고 있는 문서를 돌려준다
                return JSONResponse({"document": saved, "created": False, "detected": cover}, status_code=200)
            if not same_company(cover["company_name"], company["name"]):
                store.set_status(conn, doc_id, "uploaded", warning=f"{MISMATCH}cover page says "
                                 f"'{cover['company_name']}', but this folder is '{company['name']}'")
            saved = store.get_document(conn, doc_id)

        final = DATA / doc["file"]
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(tmp, final)
        worker().submit(doc_id)
        return {"document": saved, "created": True, "detected": cover}
    finally:
        tmp.unlink(missing_ok=True)  # 옮겼으면 이미 없다


# --- 상태, 수정, 다시 처리 ---------------------------------------------------------------

def get_or_404(conn, doc_id: str) -> dict:
    doc = store.get_document(conn, doc_id)
    if not doc:
        raise HTTPException(404, f"unknown document: {doc_id}")
    return doc


@router.get("/documents/{doc_id}")
def document_status(doc_id: str):
    with db() as conn:
        return get_or_404(conn, doc_id)


class MetadataIn(BaseModel):
    fiscal_year: int | None = Field(default=None, ge=1990, le=2100)
    period_end: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    note: str = Field(default="", max_length=500)


@router.patch("/documents/{doc_id}", status_code=202)
def correct_metadata(doc_id: str, body: MetadataIn):
    """표지 읽기가 틀렸을 때. 고친 기록은 피드백으로 남긴다: 어떤 표지에서 규칙이 틀리는지 모으려고."""
    changes = body.model_dump(exclude_none=True, exclude={"note"})
    if not changes:
        raise HTTPException(400, "nothing to change")
    with db() as conn:
        doc = get_or_404(conn, doc_id)
        if doc["status"] not in ("ready", "failed"):
            raise HTTPException(409, f"document is still processing ({doc['status']})")
        before = {k: doc[k] for k in changes}
        store.update_metadata(conn, doc_id, **changes)
        store.add_feedback(conn, "metadata", body.note or "cover page detection corrected", doc_id,
                           {"before": before, "after": changes})
        store.set_status(conn, doc_id, "uploaded", warning=doc["warning"])
    worker().submit(doc_id)  # 라벨('FY2025 10-K')이 청크에 들어 있어서 다시 처리한다. 파싱은 캐시에서 온다
    return {"doc_id": doc_id, "status": "uploaded", "changed": changes}


@router.post("/documents/{doc_id}/retry", status_code=202)
def retry(doc_id: str):
    with db() as conn:
        doc = get_or_404(conn, doc_id)
        if doc["status"] not in ("failed", "ready"):  # ready도 허용: 추출 규칙을 고친 뒤 다시 돌릴 때
            raise HTTPException(409, f"document is still processing ({doc['status']})")
        store.set_status(conn, doc_id, "uploaded", warning=doc["warning"])
    worker().submit(doc_id)
    return {"doc_id": doc_id, "status": "uploaded"}


# --- 피드백 ------------------------------------------------------------------------------

class FeedbackIn(BaseModel):
    kind: str = Field(pattern="^(metadata|answer|number|other)$")
    message: str = Field(min_length=3, max_length=2000)
    doc_id: str | None = None
    context: dict = Field(default_factory=dict)  # 질문, 답변 문장, 어떤 숫자가 틀렸나


@router.post("/feedback", status_code=201)
def post_feedback(body: FeedbackIn):
    with db() as conn:
        if body.doc_id:
            get_or_404(conn, body.doc_id)
        return {"id": store.add_feedback(conn, body.kind, body.message, body.doc_id, body.context)}