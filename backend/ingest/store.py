"""Document registry in SQLite: companies, documents, processing status (Phase 9, decision 033).

Why not data/sources.json any more: uploads change the registry while the server runs.
A JSON file is rewritten whole (read -> modify -> write), so two uploads finishing together
can lose one entry, and a crash mid-write corrupts the file. SQLite writes one row in a
transaction instead.

data/sources.json stays as the committed seed: `seed()` copies it into the database.

    uv run --project backend python backend/ingest/store.py seed     # create/refresh from sources.json
    uv run --project backend python backend/ingest/store.py list
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("APP_DB", "data/app.db"))
SOURCES = Path("data/sources.json")

# 처리 단계. ready만 질문과 대시보드에 쓰인다. failed는 error 칸에 이유가 남는다.
STATUSES = ("uploaded", "parsing", "chunking", "indexing", "extracting", "ready", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    company_id TEXT PRIMARY KEY,            -- 'amat', 'lrcx': 폴더 = 회사
    name       TEXT NOT NULL,
    ticker     TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    doc_id             TEXT PRIMARY KEY,    -- 'lrcx-10k-fy2025'
    company_id         TEXT NOT NULL REFERENCES companies(company_id),
    doc_type           TEXT NOT NULL,       -- '10-K' | 'DEF 14A'
    fiscal_year        INTEGER,
    period_end         TEXT,
    file               TEXT NOT NULL,
    sha256             TEXT NOT NULL,
    exhibit_start_page INTEGER,
    source_url         TEXT NOT NULL DEFAULT '',
    filing_url         TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL,
    error              TEXT,                -- failed: 왜 실패했나
    warning            TEXT,                -- ready지만 확인이 필요한 것 (숫자 검산 실패, 폴더와 표지의 회사가 다름)
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE (company_id, sha256)             -- 같은 폴더에 같은 파일 두 번 = 하나
);
CREATE TABLE IF NOT EXISTS feedback (       -- 사용자 리포트: 표지 정보, 답변, 숫자가 틀렸을 때
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id     TEXT,
    kind       TEXT NOT NULL,               -- 'metadata' | 'answer' | 'number' | 'other'
    message    TEXT NOT NULL,
    context    TEXT NOT NULL DEFAULT '{}',  -- JSON: 질문, 문장, 기존 값 -> 고친 값
    created_at TEXT NOT NULL
);
"""

# 이미 만들어진 app.db에는 CREATE TABLE IF NOT EXISTS가 새 칸을 더하지 않는다.
# 그래서 칸을 추가할 때는 여기에 적는다 (아주 작은 마이그레이션).
MIGRATIONS = [("documents", "warning", "TEXT")]

UNFINISHED = ("uploaded", "parsing", "chunking", "indexing", "extracting")
FEEDBACK_KINDS = ("metadata", "answer", "number", "other")

# 예전 sources.json과 같은 모양의 dict를 돌려준다. 그래서 registry.py를 쓰던 코드는 그대로 동작한다.
DOC_COLUMNS = """d.doc_id, d.company_id, c.name AS company, c.ticker, d.doc_type, d.fiscal_year, d.period_end,
    d.file, d.sha256, d.exhibit_start_page, d.source_url, d.filing_url, d.status, d.error, d.warning, d.updated_at"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """연결을 열고 표가 없으면 만든다. 연결은 스레드마다 따로 연다 (sqlite3 연결은 스레드 간에 공유하지 않는다)."""
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)  # 다른 연결이 쓰는 중이면 10초까지 기다린다
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite는 기본값이 OFF다
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    for table, column, kind in MIGRATIONS:
        columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            with conn:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def add_company(conn: sqlite3.Connection, company_id: str, name: str, ticker: str | None = None) -> None:
    with conn:  # with conn = 트랜잭션: 블록이 끝나면 commit, 예외가 나면 rollback
        conn.execute("INSERT OR IGNORE INTO companies VALUES (?, ?, ?, ?)", (company_id, name, ticker, now()))


def add_document(conn: sqlite3.Connection, doc: dict, status: str = "uploaded") -> tuple[dict, bool]:
    """문서 한 행을 넣는다. (문서, 새로 만들었나)를 돌려준다.

    같은 회사에 같은 sha256이 이미 있으면 새로 만들지 않고 기존 문서를 돌려준다 (멱등성).
    파싱 비용이 두 번 나가지 않는다. 다른 회사 폴더라면 새 문서다.
    """
    check_status(status)
    t = now()
    try:
        with conn:
            conn.execute(
                "INSERT INTO documents (doc_id, company_id, doc_type, fiscal_year, period_end, file, sha256,"
                " exhibit_start_page, source_url, filing_url, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (doc["doc_id"], doc["company_id"], doc["doc_type"], doc.get("fiscal_year"), doc.get("period_end"),
                 doc["file"], doc["sha256"], doc.get("exhibit_start_page"), doc.get("source_url") or "",
                 doc.get("filing_url") or "", status, t, t),
            )
    except sqlite3.IntegrityError:
        same_file = conn.execute("SELECT doc_id FROM documents WHERE company_id = ? AND sha256 = ?",
                                 (doc["company_id"], doc["sha256"])).fetchone()
        if same_file:
            return get_document(conn, same_file["doc_id"]), False
        raise  # 같은 파일이 아니라 doc_id가 겹친 것: 조용히 넘기면 안 된다
    return get_document(conn, doc["doc_id"]), True


def check_status(status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status '{status}'. allowed: {', '.join(STATUSES)}")


def set_status(conn: sqlite3.Connection, doc_id: str, status: str, error: str | None = None,
               warning: str | None = None) -> None:
    """상태를 바꾼다. error와 warning은 매번 새로 쓴다: 다시 처리하면 지난번 메시지는 지워진다."""
    check_status(status)
    with conn:
        cur = conn.execute("UPDATE documents SET status = ?, error = ?, warning = ?, updated_at = ? WHERE doc_id = ?",
                           (status, error, warning, now(), doc_id))
    if cur.rowcount == 0:
        raise KeyError(doc_id)


def update_metadata(conn: sqlite3.Connection, doc_id: str, **fields) -> None:
    """사용자가 표지에서 읽은 값을 고친다. 다시 처리해야 청크의 라벨('FY2025 10-K')에 반영된다."""
    allowed = {"doc_type", "fiscal_year", "period_end"}
    if not fields or set(fields) - allowed:
        raise ValueError(f"can only change: {', '.join(sorted(allowed))}")
    sets = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        cur = conn.execute(f"UPDATE documents SET {sets}, updated_at = ? WHERE doc_id = ?",
                           (*fields.values(), now(), doc_id))
    if cur.rowcount == 0:
        raise KeyError(doc_id)


def unfinished(conn: sqlite3.Connection) -> list[str]:
    """처리가 끝나지 않은 문서. 서버가 꺼질 때 하던 일이다 (고아 작업): 켜질 때 다시 대기열에 넣는다."""
    marks = ", ".join("?" * len(UNFINISHED))
    return [r["doc_id"] for r in conn.execute(
        f"SELECT doc_id FROM documents WHERE status IN ({marks}) ORDER BY created_at", UNFINISHED)]


def add_feedback(conn: sqlite3.Connection, kind: str, message: str, doc_id: str | None = None,
                 context: dict | None = None) -> int:
    if kind not in FEEDBACK_KINDS:
        raise ValueError(f"unknown feedback kind '{kind}'")
    with conn:
        cur = conn.execute("INSERT INTO feedback (doc_id, kind, message, context, created_at) VALUES (?, ?, ?, ?, ?)",
                           (doc_id, kind, message, json.dumps(context or {}), now()))
    return cur.lastrowid


def get_company(conn: sqlite3.Connection, company_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM companies WHERE company_id = ?", (company_id,)).fetchone()
    return dict(row) if row else None


def get_document(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    row = conn.execute(f"SELECT {DOC_COLUMNS} FROM documents d JOIN companies c USING (company_id)"
                       " WHERE d.doc_id = ?", (doc_id,)).fetchone()
    return dict(row) if row else None


def list_documents(conn: sqlite3.Connection, company_id: str | None = None,
                   status: str | None = "ready") -> list[dict]:
    """기본은 ready 문서만. status=None이면 처리 중인 것까지 전부."""
    sql = f"SELECT {DOC_COLUMNS} FROM documents d JOIN companies c USING (company_id) WHERE 1 = 1"
    args: list = []
    if company_id:
        sql += " AND d.company_id = ?"
        args.append(company_id)
    if status:
        sql += " AND d.status = ?"
        args.append(status)
    return [dict(r) for r in conn.execute(sql + " ORDER BY d.doc_id", args)]


def list_companies(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM companies ORDER BY company_id")]


def seed(conn: sqlite3.Connection, sources: list[dict]) -> int:
    """sources.json의 문서들을 넣는다. 이미 처리를 마친 문서라서 상태는 ready. 몇 번 돌려도 같은 결과다."""
    added = 0
    for s in sources:
        company_id = s.get("company_id") or s["ticker"].lower()
        add_company(conn, company_id, s["company"], s.get("ticker"))
        _, created = add_document(conn, {**s, "company_id": company_id}, status="ready")
        added += created
    return added


def load_seed(path: Path = SOURCES) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite document registry")
    parser.add_argument("command", choices=["seed", "list"])
    args = parser.parse_args()
    conn = connect()
    if args.command == "seed":
        print(f"added {seed(conn, load_seed())} documents to {DB_PATH}")
    for d in list_documents(conn, status=None):
        print(f"{d['company_id']:6} {d['doc_id']:20} {d['doc_type']:8} FY{d['fiscal_year']}  {d['status']}")


if __name__ == "__main__":
    main()