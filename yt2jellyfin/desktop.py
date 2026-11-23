"""Desktop entry: native window around the existing web UI (no browser chrome)."""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from yt2jellyfin import __version__
from yt2jellyfin.config import app_dir, find_config, is_frozen, load_config

log = logging.getLogger(__name__)

DEFAULT_LIBRARY = "YouTube"


def _ensure_stdio() -> Path | None:
    """Windowed (console=False) exes get stdout/stderr = None.

    Redirect them to a log file next to the exe so uvicorn/ytdlp logging
    doesn't crash and users have a file to attach to bug reports.
    Returns the log path (frozen only), else None.
    """
    if not is_frozen():
        return None
    log_path = app_dir() / "yt2jellyfin.log"
    try:
        stream = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    return log_path


def _setup_logging(log_path: Path | None) -> None:
    if log_path:
        # frozen: sys.stderr already points at the log file — one handler only
        handlers: list[logging.Handler] = [logging.FileHandler(log_path, encoding="utf-8")]
    else:
        handlers = [logging.StreamHandler(sys.stderr)]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)


def _ensure_config() -> Path:
    """Create a portable config.yaml next to the exe on first run."""
    existing = find_config()
    if existing:
        return existing

    dest = app_dir() / "config.yaml"
    if dest.is_file():
        return dest

    # Prefer a user-writable library folder when the exe dir is not writable.
    library = Path.home() / "Videos" / DEFAULT_LIBRARY
    try:
        library.mkdir(parents=True, exist_ok=True)
    except OSError:
        library = Path.home() / DEFAULT_LIBRARY
        library.mkdir(parents=True, exist_ok=True)

    example = Path(__file__).resolve().parent.parent / "config.example.yaml"
    if example.is_file():
        text = example.read_text(encoding="utf-8")
        # Replace the example library_path line with a real default.
        lines = []
        for line in text.splitlines():
            if line.startswith("library_path:"):
                lines.append(f'library_path: "{library.as_posix()}"')
            else:
                lines.append(line)
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        dest.write_text(
            f'library_path: "{library.as_posix()}"\n'
            'download_archive: ".yt2jellyfin/archive.txt"\n'
            "quality:\n  max_height: 1080\n"
            "subtitles:\n  enabled: true\n  embed: true\n"
            "metadata:\n  nfo: true\n  poster: true\n  fanart: true\n"
            "download:\n  concurrent: 1\n  retries: 3\n"
            "web:\n  host: \"127.0.0.1\"\n  port: 8392\n",
            encoding="utf-8",
        )
    return dest


def _free_port(host: str, preferred: int) -> int:
    for port in (preferred, preferred + 1, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, port))
                return s.getsockname()[1]
        except OSError:
            continue
    raise RuntimeError("No free port available for the local web UI")


def _wait_healthy(url: str, timeout: float = 15.0) -> bool:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.15)
    return False


def main() -> int:
    log_path = _ensure_stdio()
    _setup_logging(log_path)

    try:
        config_path = _ensure_config()
        cfg = load_config(config_path)
    except Exception as exc:  # noqa: BLE001
        # Fall back to a browser error page rather than a silent crash.
        print(f"yt2jellyfin: config error: {exc}", file=sys.stderr)
        if is_frozen():
            webbrowser.open("about:blank")
        return 1

    host = cfg.web.host or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    port = _free_port(host, int(cfg.web.port or 8392))
    cfg.web.host = host
    cfg.web.port = port
    base = f"http://{host}:{port}"

    import uvicorn

    from yt2jellyfin.web import create_app

    app = create_app(cfg)
    # log_config=None: skip uvicorn's dictConfig — its formatter touches
    # sys.stderr.isatty(), which is fine now but lets our own logging rule.
    config = uvicorn.Config(
        app, host=host, port=port, log_level="warning", access_log=False, log_config=None
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, name="yt2jellyfin-uvicorn", daemon=True)
    thread.start()

    if not _wait_healthy(base + "/api/health"):
        print("yt2jellyfin: local server failed to start", file=sys.stderr)
        server.should_exit = True
        return 1

    log.info("yt2jellyfin %s ready at %s (library: %s)", __version__, base, cfg.library_path)

    try:
        import webview  # pywebview
    except ImportError:
        log.warning("pywebview not installed — opening default browser")
        webbrowser.open(base)
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            server.should_exit = True
        return 0

    icon = Path(__file__).resolve().parent.parent / "extension" / "icons" / "icon128.png"
    window = webview.create_window(
        "yt2jellyfin",
        url=base,
        width=1100,
        height=760,
        min_size=(720, 480),
        background_color="#0f1419",
    )
    try:
        webview.start(debug=False)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
