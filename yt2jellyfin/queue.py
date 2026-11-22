from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from yt2jellyfin.config import Config
from yt2jellyfin.downloader import download_url
from yt2jellyfin.library import ensure_dir

log = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    url: str
    status: str
    attempts: int
    max_attempts: int
    progress: float
    message: str
    error: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    title: str | None
    output: str | None
    next_run_at: float | None = None
    dest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "title": self.title,
            "output": self.output,
            "next_run_at": self.next_run_at,
            "dest": self.dest,
        }


class JobQueue:
    """SQLite-backed job queue shared by CLI and web worker."""

    def __init__(self, db_path: Path, *, retry_delay: float = 30.0):
        ensure_dir(db_path.parent)
        self.db_path = db_path
        self.retry_delay = retry_delay
        self._lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    progress REAL NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    title TEXT,
                    output TEXT,
                    next_run_at REAL,
                    dest TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, next_run_at, created_at)"
            )
            # migrate older DBs
            cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
            if "next_run_at" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN next_run_at REAL")
            if "dest" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN dest TEXT")
            # recover jobs orphaned by a previous crash/shutdown
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, message = ?, error = NULL, next_run_at = NULL, updated_at = ?
                WHERE status = ?
                """,
                (STATUS_PENDING, "Re-queued after restart", _utcnow(), STATUS_RUNNING),
            )

    def add(self, url: str, *, max_attempts: int = 3, dest: str | None = None) -> Job:
        job_id = uuid.uuid4().hex[:12]
        now = _utcnow()
        url = url.strip()
        dest_norm = str(Path(dest).expanduser()) if dest else None
        with self._conn() as conn:
            # de-dupe identical pending/running URLs to the same destination
            row = conn.execute(
                "SELECT id FROM jobs WHERE url = ? AND status IN (?, ?) AND dest IS ?",
                (url, STATUS_PENDING, STATUS_RUNNING, dest_norm),
            ).fetchone()
            if row:
                existing = self.get(str(row["id"]))
                if existing:
                    return existing
            conn.execute(
                """
                INSERT INTO jobs (id, url, status, attempts, max_attempts, message, created_at, updated_at, next_run_at, dest)
                VALUES (?, ?, ?, 0, ?, ?, ?, ?, NULL, ?)
                """,
                (job_id, url, STATUS_PENDING, max_attempts, "Queued", now, now, dest_norm),
            )
        job = self.get(job_id)
        assert job is not None
        return job

    def get(self, job_id: str) -> Job | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list(self, *, status: str | None = None, limit: int = 200) -> list[Job]:
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def counts(self) -> dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        out = {s: 0 for s in (STATUS_PENDING, STATUS_RUNNING, STATUS_DONE, STATUS_FAILED, STATUS_SKIPPED)}
        for r in rows:
            out[r["status"]] = r["n"]
        return out

    def claim_next(self) -> Job | None:
        """Atomically claim the oldest pending job that is due to run."""
        with self._lock:
            with self._conn() as conn:
                now_ts = time.time()
                row = conn.execute(
                    """
                    SELECT id FROM jobs
                    WHERE status = ?
                      AND (next_run_at IS NULL OR next_run_at <= ?)
                    ORDER BY created_at ASC, rowid ASC LIMIT 1
                    """,
                    (STATUS_PENDING, now_ts),
                ).fetchone()
                if not row:
                    return None
                job_id = str(row["id"])
                now = _utcnow()
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, started_at = ?, updated_at = ?, message = ?,
                        error = NULL, next_run_at = NULL
                    WHERE id = ? AND status = ?
                    """,
                    (STATUS_RUNNING, now, now, "Starting", job_id, STATUS_PENDING),
                )
                row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def update_progress(self, job_id: str, progress: float, message: str = "") -> None:
        with self._conn() as conn:
            if message:
                conn.execute(
                    "UPDATE jobs SET progress = ?, message = ?, updated_at = ? WHERE id = ?",
                    (progress, message, _utcnow(), job_id),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET progress = ?, updated_at = ? WHERE id = ?",
                    (progress, _utcnow(), job_id),
                )

    def mark_done(self, job_id: str, *, title: str | None = None, output: str | None = None) -> None:
        now = _utcnow()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, progress = 100, message = ?, title = COALESCE(?, title),
                    output = COALESCE(?, output), finished_at = ?, updated_at = ?, error = NULL
                WHERE id = ?
                """,
                (STATUS_DONE, "Completed", title, output, now, now, job_id),
            )

    def mark_failed(self, job_id: str, error: str, *, retry: bool) -> None:
        now = _utcnow()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT attempts, max_attempts FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not row:
                return
            attempts = int(row["attempts"]) + 1
            max_attempts = int(row["max_attempts"])
            if retry and attempts < max_attempts:
                delay = float(self.retry_delay) * min(2 ** max(attempts - 1, 0), 8)
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, attempts = ?, error = ?, message = ?, updated_at = ?,
                        next_run_at = ?
                    WHERE id = ?
                    """,
                    (
                        STATUS_PENDING,
                        attempts,
                        error,
                        f"Retry {attempts}/{max_attempts} in {int(delay)}s",
                        now,
                        time.time() + delay,
                        job_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, attempts = ?, error = ?, message = ?, finished_at = ?,
                        updated_at = ?, next_run_at = NULL
                    WHERE id = ?
                    """,
                    (STATUS_FAILED, attempts, error, "Failed", now, now, job_id),
                )

    def mark_skipped(self, job_id: str, message: str) -> None:
        now = _utcnow()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, message = ?, finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (STATUS_SKIPPED, message, now, now, job_id),
            )

    def reset(self, job_id: str) -> Job | None:
        now = _utcnow()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, attempts = 0, progress = 0, message = ?, error = NULL,
                    started_at = NULL, finished_at = NULL, updated_at = ?, next_run_at = NULL
                WHERE id = ?
                """,
                (STATUS_PENDING, "Queued", now, job_id),
            )
        return self.get(job_id)

    def delete(self, job_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return cur.rowcount > 0

    def clear_finished(self) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM jobs WHERE status IN (?, ?, ?)",
                (STATUS_DONE, STATUS_FAILED, STATUS_SKIPPED),
            )
        return cur.rowcount

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            url=row["url"],
            status=row["status"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            progress=row["progress"],
            message=row["message"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            title=row["title"],
            output=row["output"],
            next_run_at=row["next_run_at"] if "next_run_at" in row.keys() else None,
            dest=row["dest"] if "dest" in row.keys() else None,
        )


class Worker:
    """Background worker that drains the queue with retries."""

    def __init__(self, cfg: Config, queue: JobQueue):
        self.cfg = cfg
        self.queue = queue
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active = 0
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="yt2jellyfin-worker", daemon=True)
        self._thread.start()
        log.info("Worker started")

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        log.info("Worker stopped")

    def _loop(self) -> None:
        concurrency = max(1, int(self.cfg.download.concurrent or 1))
        threads: list[threading.Thread] = []
        while not self._stop.is_set():
            # reap finished threads
            threads = [t for t in threads if t.is_alive()]
            if len(threads) < concurrency:
                job = self.queue.claim_next()
                if job:
                    t = threading.Thread(
                        target=self._run_job, args=(job,), name=f"job-{job.id}", daemon=True
                    )
                    t.start()
                    threads.append(t)
                    continue
            self._stop.wait(0.5)
        # wait for in-flight
        for t in threads:
            t.join(timeout=30)

    def _run_job(self, job: Job) -> None:
        with self._lock:
            self._active += 1
        try:
            last_percent = -1.0

            def hook(d: dict[str, Any]) -> None:
                nonlocal last_percent
                status = d.get("status")
                if status == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    done = d.get("downloaded_bytes") or 0
                    if total:
                        pct = round(done * 100.0 / total, 1)
                        if pct != last_percent:
                            last_percent = pct
                            self.queue.update_progress(job.id, pct, f"Downloading {pct:.0f}%")
                elif status == "finished":
                    self.queue.update_progress(job.id, 95, "Post-processing")
                elif status == "error":
                    self.queue.update_progress(job.id, last_percent if last_percent >= 0 else 0, "Error")

            cfg = self.cfg
            if job.dest:
                # per-job destination override
                import dataclasses

                cfg = dataclasses.replace(self.cfg, library_path=Path(job.dest))
            result = download_url(cfg, job.url, progress_hook=hook)
            title = result.title or None
            outputs = [str(p) for p in result.video_files]
            output = outputs[0] if outputs else None
            self.queue.mark_done(job.id, title=title, output=output)
            log.info("Job %s done: %s", job.id, job.url)
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            log.error("Job %s failed: %s\n%s", job.id, err, traceback.format_exc())
            self.queue.mark_failed(job.id, err, retry=True)
        finally:
            with self._lock:
                self._active -= 1
