from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from yt2jellyfin import __version__
from yt2jellyfin.config import load_config
from yt2jellyfin.library import ensure_dir
from yt2jellyfin.queue import STATUS_DONE, STATUS_FAILED, STATUS_PENDING, JobQueue, Worker


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("yt_dlp").setLevel(logging.WARNING if not verbose else logging.DEBUG)


def _make_queue(cfg) -> JobQueue:
    ensure_dir(cfg.resolved_state_dir)
    return JobQueue(cfg.queue_db, retry_delay=float(cfg.download.retry_delay or 30))


def cmd_download(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    queue = _make_queue(cfg)
    dest = str(Path(args.output).expanduser()) if args.output else None
    if dest:
        ensure_dir(Path(dest))
        print(f"Destination: {dest}")
    jobs = [
        queue.add(u, max_attempts=max(1, cfg.download.retries + 1), dest=dest)
        for u in args.urls
    ]
    print(f"Queued {len(jobs)} job(s) into {dest or cfg.library_path}")
    for j in jobs:
        print(f"  [{j.id}] {j.url}")

    if args.wait:
        worker = Worker(cfg, queue)
        worker.start()
        try:
            while True:
                pending = queue.counts()
                active = pending.get("pending", 0) + pending.get("running", 0)
                if active == 0:
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nInterrupted — downloads keep running only if worker still active; stopping.")
            worker.stop()
            return 130
        finally:
            worker.stop()

        failed = queue.list(status=STATUS_FAILED)
        done = queue.list(status=STATUS_DONE)
        print(f"Finished: {len(done)} done, {len(failed)} failed")
        for j in failed:
            print(f"  FAILED {j.url}: {j.error}")
        return 1 if failed else 0
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    queue = _make_queue(cfg)

    if args.queue_cmd == "list":
        status = args.status
        jobs = queue.list(status=status)
        if args.json:
            print(json.dumps([j.to_dict() for j in jobs], indent=2))
            return 0
        if not jobs:
            print("Queue is empty.")
            return 0
        for j in jobs:
            err = f" | {j.error}" if j.error else ""
            print(
                f"{j.id}  {j.status:8}  {j.progress:5.1f}%  {j.url}  "
                f"({j.message}){err}"
            )
        return 0

    if args.queue_cmd == "clear":
        n = queue.clear_finished()
        print(f"Removed {n} finished job(s).")
        return 0

    if args.queue_cmd == "rm":
        ok = queue.delete(args.id)
        print("Deleted." if ok else "Job not found.")
        return 0 if ok else 1

    if args.queue_cmd == "retry":
        job = queue.reset(args.id)
        if not job:
            print("Job not found.")
            return 1
        print(f"Re-queued {job.id}: {job.url}")
        return 0

    print("Unknown queue subcommand", file=sys.stderr)
    return 2


def cmd_worker(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    queue = _make_queue(cfg)
    worker = Worker(cfg, queue)
    print(f"Worker running (library: {cfg.library_path}, db: {cfg.queue_db})")
    print("Press Ctrl+C to stop.")
    worker.start()
    try:
        while worker.running:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        worker.stop()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.host:
        cfg.web.host = args.host
    if args.port:
        cfg.web.port = args.port
    import uvicorn

    from yt2jellyfin.web import create_app

    app = create_app(cfg)
    print(f"Web UI: http://{cfg.web.host}:{cfg.web.port}")
    print(f"Library: {cfg.library_path}")
    uvicorn.run(app, host=cfg.web.host, port=cfg.web.port, log_level="info")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    if args.config_cmd == "path":
        cfg = load_config(args.config)
        print(cfg.config_path or "(no config file found — using defaults)")
        print(f"library_path: {cfg.library_path}")
        print(f"state_dir:    {cfg.resolved_state_dir}")
        print(f"queue_db:     {cfg.queue_db}")
        return 0
    if args.config_cmd == "init":
        dest = Path(args.path or "config.yaml")
        if dest.exists() and not args.force:
            print(f"{dest} already exists (use --force to overwrite).", file=sys.stderr)
            return 1
        example = Path(__file__).resolve().parent.parent / "config.example.yaml"
        if example.is_file():
            dest.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            dest.write_text(
                'library_path: "D:/media/youtube"\n',
                encoding="utf-8",
            )
        print(f"Wrote {dest} — edit library_path to your Jellyfin library folder.")
        return 0
    return 2


def cmd_info(args: argparse.Namespace) -> int:
    from yt2jellyfin.downloader import probe_info

    cfg = load_config(args.config)
    info = probe_info(cfg, args.url)
    # keep output small
    keys = [
        "id", "title", "channel", "uploader", "upload_date", "duration",
        "playlist_title", "playlist_count", "webpage_url", "extractor",
    ]
    slim = {k: info.get(k) for k in keys if k in info}
    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
        slim["entries"] = len(entries)
        slim["sample"] = [
            {"id": e.get("id"), "title": e.get("title")}
            for e in entries[:5]
            if isinstance(e, dict)
        ]
    print(json.dumps(slim, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yt2jellyfin",
        description="Download YouTube videos into a Jellyfin library (with NFO metadata).",
    )
    p.add_argument("-c", "--config", help="Path to config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("download", help="Queue one or more YouTube URLs")
    d.add_argument("urls", nargs="+", help="Video, playlist, or channel URLs")
    d.add_argument(
        "-o",
        "--output",
        help="Download folder (default: library_path from config.yaml)",
    )
    d.add_argument("-w", "--wait", action="store_true", help="Run worker until queue is empty")
    d.set_defaults(func=cmd_download)

    q = sub.add_parser("queue", help="Inspect / manage the job queue")
    qsub = q.add_subparsers(dest="queue_cmd", required=True)
    ql = qsub.add_parser("list", help="List jobs")
    ql.add_argument("--status", choices=[STATUS_PENDING, "running", STATUS_DONE, STATUS_FAILED, "skipped"])
    ql.add_argument("--json", action="store_true")
    ql.set_defaults(func=cmd_queue)
    qc = qsub.add_parser("clear", help="Remove finished jobs")
    qc.set_defaults(func=cmd_queue)
    qr = qsub.add_parser("rm", help="Delete a job")
    qr.add_argument("id")
    qr.set_defaults(func=cmd_queue)
    qq = qsub.add_parser("retry", help="Re-queue a failed job")
    qq.add_argument("id")
    qq.set_defaults(func=cmd_queue)

    w = sub.add_parser("worker", help="Run background worker (queue consumer)")
    w.set_defaults(func=cmd_worker)

    s = sub.add_parser("serve", help="Start web UI + worker")
    s.add_argument("--host", help="Bind host (default from config)")
    s.add_argument("--port", type=int, help="Bind port (default from config)")
    s.set_defaults(func=cmd_serve)

    c = sub.add_parser("config", help="Config helpers")
    csub = c.add_subparsers(dest="config_cmd", required=True)
    cp = csub.add_parser("path", help="Show resolved config paths")
    cp.set_defaults(func=cmd_config)
    ci = csub.add_parser("init", help="Create config.yaml from example")
    ci.add_argument("--path", help="Destination path (default: ./config.yaml)")
    ci.add_argument("--force", action="store_true")
    ci.set_defaults(func=cmd_config)

    i = sub.add_parser("info", help="Probe a URL without downloading")
    i.add_argument("url")
    i.set_defaults(func=cmd_info)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
