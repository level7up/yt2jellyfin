from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from yt2jellyfin.config import Config
from yt2jellyfin.library import ensure_dir
from yt2jellyfin.metadata import write_artwork, write_nfo

log = logging.getLogger(__name__)

ProgressHook = Callable[[dict[str, Any]], None]


def _ffmpeg_available() -> bool:
    import shutil

    return shutil.which("ffmpeg") is not None


def _require_ffmpeg() -> None:
    if not _ffmpeg_available():
        raise RuntimeError(
            "ffmpeg is required to merge YouTube video+audio streams. "
            "Install ffmpeg and ensure it is on PATH: https://ffmpeg.org/download.html"
        )


def _probe_streams(path: Path) -> list[dict[str, str]]:
    """Return stream info via ffprobe: [{codec_type, codec_name}, ...]."""
    import json
    import shutil
    import subprocess

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return []
    try:
        out = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-print_format", "json",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if out.returncode != 0:
            return []
        data = json.loads(out.stdout or "{}")
        return data.get("streams") or []
    except Exception:  # noqa: BLE001
        return []


def _validate_av(path: Path) -> None:
    """Fail loudly if the file has no audio (or no video) — protects Jellyfin playback."""
    streams = _probe_streams(path)
    if not streams:
        log.warning("Could not probe %s (ffprobe missing?); skipping AV validation", path)
        return
    types = {s.get("codec_type") for s in streams}
    if "video" not in types:
        raise RuntimeError(f"Downloaded file has no video stream: {path}")
    if "audio" not in types:
        raise RuntimeError(
            f"Downloaded file has no audio stream: {path}. "
            "Delete it and re-download after updating yt2jellyfin."
        )


@dataclass
class DownloadResult:
    video_files: list[Path] = field(default_factory=list)
    info: dict[str, Any] | None = None
    title: str = ""


def build_format_selector(cfg: Config) -> str:
    q = cfg.quality
    if q.format:
        return q.format
    h = q.max_height
    if not h:
        # Always pair best video with best audio; fall back to progressive b.
        return "bv*+ba/b"
    # NOTE: do NOT filter audio (ba) by height — audio formats have no height,
    # so ba[height<=…] matches nothing and yt-dlp falls back to video-only.
    return f"bv*[height<={h}]+ba/b[height<={h}]/bv*[height<={h}]"


def build_outtmpl(cfg: Config) -> str:
    # Files go directly into the library root (no channel / title folders).
    return "%(title)s [%(id)s].%(ext)s"


def _make_ydl_opts(
    cfg: Config,
    *,
    outtmpl: str,
    progress_hooks: list[ProgressHook],
    quiet: bool = True,
) -> dict[str, Any]:
    has_ffmpeg = _ffmpeg_available()
    opts: dict[str, Any] = {
        "outtmpl": {"default": outtmpl},
        "format": build_format_selector(cfg),
        "quiet": quiet,
        "no_warnings": quiet,
        "progress_hooks": progress_hooks,
        "postprocess_hooks": [],
        "retries": 2,
        "fragment_retries": 5,
        "ignoreerrors": False,
        "writethumbnail": False,
        "playlist_items": cfg.download.playlist_items or None,
        "windowsfilenames": True,
    }
    if has_ffmpeg:
        opts["merge_output_format"] = "mp4"
        opts["format_sort"] = ["res", "ext:mp4:m4a"]

    if cfg.download_archive:
        # Archive is per destination: re-downloading the same video into a
        # different folder must NOT be skipped just because it exists elsewhere.
        archive = Path(cfg.download_archive)
        default_lib = cfg.default_library_path or cfg.library_path
        if Path(cfg.library_path) != Path(default_lib):
            import hashlib

            key = hashlib.sha1(str(Path(cfg.library_path)).encode("utf-8", "replace")).hexdigest()[:10]
            archive = archive.with_name(f"{archive.stem}-{key}{archive.suffix}")
        ensure_dir(archive.parent)
        opts["download_archive"] = str(archive)

    if cfg.download.sleep:
        opts["sleep_requests"] = cfg.download.sleep

    if cfg.download.cookiefile:
        opts["cookiefile"] = str(cfg.download.cookiefile)
    elif cfg.download.cookies_from_browser:
        opts["cookiesfrombrowser"] = (cfg.download.cookies_from_browser,)

    # YouTube needs a JS runtime + EJS solver for many videos; use node if present.
    import shutil

    if shutil.which("node"):
        opts["js_runtimes"] = {"node": {}}
        opts["remote_components"] = {"ejs:github"}

    if cfg.subtitles.enabled:
        opts["writesubtitles"] = True
        opts["subtitleslangs"] = list(cfg.subtitles.langs) or ["en"]
        can_embed = cfg.subtitles.embed and has_ffmpeg
        if can_embed:
            opts["postprocessors"] = opts.get("postprocessors", []) + [
                {
                    "key": "FFmpegEmbedSubtitle",
                    "already_have_subtitle": False,
                }
            ]
            opts["embedsubs"] = True
        elif cfg.subtitles.embed:
            log.warning("ffmpeg not found; subtitles will be sidecar files instead of embedded")
    else:
        opts["writesubtitles"] = False
        opts["subtitleslangs"] = []

    # Prefer mp4 for Jellyfin compatibility when merging (ffmpeg only).
    return opts


def _extract_video_files(record: dict[str, Any], requested_dir: Path | None) -> list[Path]:
    files: list[Path] = []
    for key in ("filename", "filepath"):
        p = record.get(key)
        if p:
            files.append(Path(p))
    if not files and requested_dir and record.get("id"):
        # Fallback: look for files matching [id]
        vid = str(record["id"])
        for p in requested_dir.rglob(f"*[{vid}].*"):
            if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".m4v", ".mov", ".avi"}:
                files.append(p)
    # de-dupe
    seen: set[str] = set()
    out: list[Path] = []
    for f in files:
        s = str(f)
        if s not in seen and f.exists():
            seen.add(s)
            out.append(f)
    return out


def download_url(
    cfg: Config,
    url: str,
    *,
    progress_hook: ProgressHook | None = None,
) -> DownloadResult:
    """Download a single video or playlist/channel URL into the Jellyfin library."""
    import yt_dlp

    _require_ffmpeg()
    ensure_dir(cfg.library_path)
    hooks: list[ProgressHook] = [progress_hook] if progress_hook else []
    opts = _make_ydl_opts(cfg, outtmpl=build_outtmpl(cfg), progress_hooks=hooks)
    has_ffmpeg = _ffmpeg_available()

    # Relative outtmpl is resolved against cwd — pin downloads to the library root.
    library_root = str(cfg.library_path).rstrip("/\\")
    default_tmpl = opts["outtmpl"]["default"]
    opts["outtmpl"]["default"] = f"{library_root}/{default_tmpl.lstrip('/')}"

    result = DownloadResult()
    log.info("Downloading: %s", url)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        result.info = info

        # Playlists return a playlist dict with entries
        entries = []
        if isinstance(info, dict):
            if info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                result.title = info.get("title") or ""
            else:
                entries = [info]
                result.title = info.get("title") or ""

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            # After download, requested_downloads / filepath hold final path
            rec = entry
            for rd in entry.get("requested_downloads") or []:
                if isinstance(rd, dict) and rd.get("filepath"):
                    rec = rd
                    break
            files = _extract_video_files(rec, cfg.library_path)

            for vf in files:
                _validate_av(vf)

            result.video_files.extend(files)

            if files and (cfg.metadata.nfo or cfg.metadata.poster or cfg.metadata.fanart):
                video_file = files[0]
                try:
                    if cfg.metadata.nfo:
                        write_nfo(video_file, entry)
                    if cfg.metadata.poster or cfg.metadata.fanart:
                        write_artwork(
                            video_file,
                            entry,
                            poster=cfg.metadata.poster,
                            fanart=cfg.metadata.fanart,
                        )
                except Exception:
                    log.exception("Metadata failed for %s", video_file)

            if (
                cfg.subtitles.sidecar is False
                and cfg.subtitles.embed
                and has_ffmpeg
                and files
            ):
                # Remove leftover .vtt/.srt sidecars after successful embed
                _cleanup_sidecar_subs(files[0], cfg)

        # Single-video case: ydl may have written filepath onto info
        if not result.video_files and isinstance(info, dict) and info.get("filepath"):
            p = Path(info["filepath"])
            if p.exists():
                result.video_files.append(p)

    if not result.video_files:
        # Download-archive skip: look for an existing file with this video id.
        if isinstance(info, dict):
            vid = info.get("id")
            if vid:
                for p in cfg.library_path.rglob(f"*[{vid}].*"):
                    if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".m4v", ".mov", ".avi"}:
                        result.video_files.append(p)
                        result.title = result.title or str(info.get("title") or "")
                        break
        if result.video_files:
            log.info("Already in library (archive skip): %s", result.video_files[0])
        else:
            log.warning("No output files detected for %s (already archived?)", url)
    return result


def _cleanup_sidecar_subs(video_file: Path, cfg: Config) -> None:
    if cfg.subtitles.sidecar:
        return
    for sub in video_file.parent.glob(video_file.stem + ".*"):
        if sub.suffix.lower() in {".vtt", ".srt", ".ass", ".ttml", ".lrc"}:
            try:
                sub.unlink()
            except OSError:
                pass


def probe_info(cfg: Config, url: str) -> dict[str, Any]:
    import yt_dlp

    import shutil

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlist_items": cfg.download.playlist_items or None,
    }
    if cfg.download.cookiefile:
        opts["cookiefile"] = str(cfg.download.cookiefile)
    elif cfg.download.cookies_from_browser:
        opts["cookiesfrombrowser"] = (cfg.download.cookies_from_browser,)
    if shutil.which("node"):
        opts["js_runtimes"] = {"node": {}}
        opts["remote_components"] = {"ejs:github"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info or {}
