from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from yt2jellyfin.config import Config
from yt2jellyfin.queue import JobQueue, Worker

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class AddJobBody(BaseModel):
    url: str = Field(min_length=8)
    wait: bool = False
    dest: str | None = None


class OpenBody(BaseModel):
    path: str = Field(min_length=1)


def create_app(cfg: Config) -> FastAPI:
    queue = JobQueue(cfg.queue_db, retry_delay=float(cfg.download.retry_delay or 30))
    worker = Worker(cfg, queue)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker.start()
        log.info("Worker started with web UI")
        try:
            yield
        finally:
            worker.stop()
            log.info("Worker stopped")

    app = FastAPI(title="yt2jellyfin", lifespan=lifespan)
    # Local tool — allow browser/extension callers.
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.cfg = cfg
    app.state.queue = queue
    app.state.worker = worker

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "library_path": str(cfg.library_path),
            "worker_running": worker.running,
            "active": worker.active,
            "counts": queue.counts(),
        }

    @app.get("/api/jobs")
    def list_jobs(status: str | None = None) -> list[dict[str, Any]]:
        return [j.to_dict() for j in queue.list(status=status)]

    @app.post("/api/jobs", status_code=201)
    def add_job(body: AddJobBody) -> dict[str, Any]:
        url = body.url.strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "URL must start with http:// or https://")
        dest: str | None = None
        if body.dest and body.dest.strip():
            dest = str(Path(body.dest.strip()).expanduser())
            try:
                Path(dest).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(400, f"Cannot create folder: {exc}") from exc
        job = queue.add(url, max_attempts=max(1, cfg.download.retries + 1), dest=dest)
        return job.to_dict()

    @app.get("/api/folders")
    def browse_folders(path: str | None = None) -> dict[str, Any]:
        """Server-side folder browser for the destination picker."""
        import string

        current = Path(path).expanduser() if path else Path(cfg.library_path)
        try:
            if not current.is_dir():
                current = Path(cfg.library_path)
        except OSError:
            current = Path(cfg.library_path)

        dirs: list[str] = []
        try:
            for child in sorted(current.iterdir(), key=lambda p: p.name.lower()):
                if child.is_dir() and not child.name.startswith("."):
                    dirs.append(child.name)
        except OSError as exc:
            raise HTTPException(400, f"Cannot list folder: {exc}") from exc

        # drive roots on Windows
        drives: list[str] = []
        if path is None and sys.platform == "win32":
            import string as _string

            for letter in _string.ascii_uppercase:
                d = Path(f"{letter}:\\")
                try:
                    if d.exists():
                        drives.append(f"{letter}:")
                except OSError:
                    # locked (BitLocker) or inaccessible drive — skip
                    continue

        parent = None
        try:
            if current.parent != current:
                parent = str(current.parent)
        except OSError:
            parent = None
        return {"path": str(current), "parent": parent, "dirs": dirs, "drives": drives}

    @app.post("/api/open")
    def open_path(body: OpenBody) -> dict[str, Any]:
        """Open a file or folder with the OS default application (local use only)."""
        import subprocess

        p = Path(body.path).expanduser()
        if not p.exists():
            raise HTTPException(404, f"Not found: {p}")
        try:
            if sys.platform == "win32":
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except OSError as exc:
            raise HTTPException(500, f"Cannot open: {exc}") from exc
        return {"opened": str(p)}

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str) -> dict[str, Any]:
        if not queue.delete(job_id):
            raise HTTPException(404, "Job not found")
        return {"deleted": job_id}

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str) -> dict[str, Any]:
        job = queue.reset(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return job.to_dict()

    @app.post("/api/jobs/clear")
    def clear_finished() -> dict[str, Any]:
        return {"removed": queue.clear_finished()}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        page = STATIC_DIR / "index.html"
        if not page.is_file():
            raise HTTPException(500, "index.html missing")
        return page.read_text(encoding="utf-8")

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
