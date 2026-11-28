"""Upload processing: parsing -> chunking -> indexing -> extracting -> ready | failed (Phase 9-2, decision 034).

Two rules make this safe to restart at any point:
1. The state lives in the database (documents.status), not in memory. The database row is the queue.
2. Every step can run twice with the same result (it replaces that document's output: files,
   Milvus chunks, facts). So after a crash the document is simply processed again from the start.
   Parsing is cached by sha256, so running again does not pay LlamaParse again.

A failed number check does not fail the document: Ask still works, and the dashboard shows a warning.
"""

import queue
import shutil
import threading
import traceback
from collections.abc import Callable

import store

STEPS = ("parsing", "chunking", "indexing", "extracting")
NUMBERS = "numbers need review: "


class NumbersNeedReview(Exception):
    """숫자 추출 단계의 경고: 문서는 ready로 두고, 대시보드에 '숫자 검토 필요'를 띄운다."""


def run(doc_id: str, steps: dict[str, Callable[[dict], None]], connect=store.connect,
        on_ready: Callable[[str], None] | None = None) -> str:
    """문서 하나를 처음부터 끝까지 처리한다. 마지막 상태를 돌려준다."""
    conn = connect()
    step = "start"
    try:
        # 업로드 때 붙은 경고(폴더와 표지의 회사가 다름)는 남기고, 지난번 처리의 숫자 경고는 지운다
        old = (store.get_document(conn, doc_id) or {}).get("warning") or ""
        warnings = [w for w in old.split("; ") if w and not w.startswith(NUMBERS)]
        for step in STEPS:
            store.set_status(conn, doc_id, step, warning="; ".join(warnings) or None)
            doc = store.get_document(conn, doc_id)  # 사용자가 메타데이터를 고쳤을 수 있다: 매번 새로 읽는다
            try:
                steps[step](doc)
            except NumbersNeedReview as e:
                if step != "extracting":
                    raise
                warnings.append(f"{NUMBERS}{e}")
        store.set_status(conn, doc_id, "ready", warning="; ".join(warnings) or None)
        if on_ready:
            on_ready(doc_id)
        return "ready"
    except Exception as e:  # noqa: BLE001 - 어떤 오류든 failed와 이유로 남긴다. 서버는 계속 돈다
        traceback.print_exc()
        store.set_status(conn, doc_id, "failed", error=f"{step}: {type(e).__name__}: {e}"[:500])
        return "failed"
    finally:
        conn.close()


class Worker:
    """한 번에 문서 하나씩 처리하는 백그라운드 스레드.

    하나씩인 이유: 임베딩 모델과 Milvus Lite를 여러 스레드가 동시에 쓰지 않게 하려고. 업로드가 몰리면
    대기열에서 기다린다(상태는 uploaded). 나중에 AWS에서는 이 자리에 별도 워커 서버와 큐가 들어간다.
    """

    def __init__(self, steps: dict, connect=store.connect, on_ready=None):
        self.steps, self.connect, self.on_ready = steps, connect, on_ready
        self.jobs: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="upload-worker")

    def start(self) -> list[str]:
        """서버가 켜질 때: 끝나지 않은 문서(고아 작업)를 다시 대기열에 넣고 스레드를 시작한다."""
        conn = self.connect()
        try:
            orphans = store.unfinished(conn)
        finally:
            conn.close()
        for doc_id in orphans:
            self.jobs.put(doc_id)
        self.thread.start()
        return orphans

    def submit(self, doc_id: str) -> None:
        self.jobs.put(doc_id)

    def stop(self, timeout: float | None = None) -> None:
        self.jobs.put(None)
        self.thread.join(timeout)

    def _loop(self) -> None:
        while (doc_id := self.jobs.get()) is not None:
            run(doc_id, self.steps, self.connect, self.on_ready)
            self.jobs.task_done()


# --- 실제 단계 (무거운 라이브러리는 여기서만 import한다) ---------------------------------

def real_steps(embedder, milvus, collection: str, parser: str = "llamaparse") -> dict:
    from chunk import chunk_document
    from index import replace_document
    from parse_llamaparse import convert, fetch
    from registry import DATA, parsed_dir

    import extract

    def parsing(doc: dict) -> None:
        out = parsed_dir(doc, parser)
        out.mkdir(parents=True, exist_ok=True)
        cache = DATA / "cache" / parser / f"{doc['sha256']}.json"  # 내용이 같으면 어느 폴더든 한 번만 낸다
        if cache.exists():
            shutil.copyfile(cache, out / "raw.json")
        else:
            fetch(doc, None)  # 유료 단계
            cache.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out / "raw.json", cache)
        for old in out.glob("page_*.json"):  # 다시 처리할 때 예전 페이지가 섞이지 않게
            old.unlink()
        convert(doc)

    def chunking(doc: dict) -> None:
        chunk_document(doc, embedder.tokenizer, parser)

    def indexing(doc: dict) -> None:
        from registry import chunks_path
        chunks = extract.load_chunks(chunks_path(doc, parser))
        replace_document(milvus, collection, doc["doc_id"], chunks, embedder)

    def extracting(doc: dict) -> None:
        from registry import chunks_path
        chunks = extract.load_chunks(chunks_path(doc, parser))
        facts, errors, gaps = extract.extract_document(doc["doc_id"], chunks)
        if errors:
            # 검산이 틀린 숫자는 저장하지 않는다: 대시보드가 틀린 워터폴을 그리는 것보다 경고가 낫다
            extract.replace_facts(doc["doc_id"], [])
            raise NumbersNeedReview(f"{len(errors)} check(s) failed, e.g. {errors[0]}")
        extract.replace_facts(doc["doc_id"], facts)
        if gaps:  # 있는 숫자는 맞으니 저장하고, 빠진 것을 알린다 (조용한 누락을 경고로 바꾼다)
            raise NumbersNeedReview("; ".join(gaps))

    return {"parsing": parsing, "chunking": chunking, "indexing": indexing, "extracting": extracting}