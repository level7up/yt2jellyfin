from __future__ import annotations

import html
import logging
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _esc(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        value = round(value, 2)
    return html.escape(str(value), quote=False)


def _year_from_info(info: dict[str, Any]) -> int | None:
    for key in ("release_year", "year"):
        y = info.get(key)
        if isinstance(y, int) and 1900 <= y <= 2100:
            return y
    release = info.get("release_date") or info.get("upload_date") or info.get("timestamp")
    if isinstance(release, str) and len(release) >= 4 and release[:4].isdigit():
        y = int(release[:4])
        if 1900 <= y <= 2100:
            return y
    if isinstance(release, (int, float)) and release:
        try:
            return datetime.utcfromtimestamp(int(release)).year
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _premiered(info: dict[str, Any]) -> str | None:
    release = info.get("release_date") or info.get("upload_date")
    if isinstance(release, str) and re.fullmatch(r"\d{8}", release):
        return f"{release[0:4]}-{release[4:6]}-{release[6:8]}"
    ts = info.get("timestamp")
    if isinstance(ts, (int, float)) and ts:
        try:
            return datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _thumb_candidates(info: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("thumbnail", "preview"):
        v = info.get(key)
        if isinstance(v, str) and v.startswith("http"):
            urls.append(v)
    thumbs = info.get("thumbnails") or []
    if isinstance(thumbs, list):
        for t in reversed(thumbs):
            if isinstance(t, dict) and isinstance(t.get("url"), str):
                urls.append(t["url"])
            elif isinstance(t, str):
                urls.append(t)
    # prefer large/mqdefault style
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def build_nfo(info: dict[str, Any], video_file: Path) -> str:
    title = info.get("title") or video_file.stem
    plot = info.get("description") or ""
    if len(plot) > 4000:
        plot = plot[:4000] + "…"
    video_id = info.get("id") or ""
    uploader = info.get("uploader") or info.get("channel") or info.get("creator")
    year = _year_from_info(info)
    premiered = _premiered(info)
    duration = info.get("duration")
    runtime = int(round(duration / 60)) if isinstance(duration, (int, float)) and duration else None
    view_count = info.get("view_count")
    like_count = info.get("like_count")
    avg_rating = None
    if isinstance(view_count, int) and view_count > 0 and isinstance(like_count, int):
        avg_rating = round((like_count / view_count) * 10.0, 2)

    genres: list[str] = []
    tags = info.get("tags") or []
    if isinstance(tags, list):
        genres = [str(t) for t in tags[:15]]

    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        "<movie>",
        f"  <title>{_esc(title)}</title>",
        f"  <plot>{_esc(plot)}</plot>",
        f"  <outline>{_esc(plot[:300])}</outline>",
    ]
    if video_id:
        lines.append(f'  <uniqueid type="youtube" default="true">{_esc(video_id)}</uniqueid>')
        lines.append(f"  <id>{_esc(video_id)}</id>")
    if premiered:
        lines.append(f"  <premiered>{_esc(premiered)}</premiered>")
    if year:
        lines.append(f"  <year>{year}</year>")
    if uploader:
        lines.append(f"  <studio>{_esc(uploader)}</studio>")
        lines.append(f"  <director>{_esc(uploader)}</director>")
    if runtime:
        lines.append(f"  <runtime>{runtime}</runtime>")
    if avg_rating is not None:
        lines.append(f"  <rating>{avg_rating}</rating>")
    if isinstance(view_count, int):
        lines.append(f"  <votes>{view_count}</votes>")
    if isinstance(like_count, int):
        lines.append(f"  <likes>{like_count}</likes>")
    for g in genres:
        lines.append(f"  <tag>{_esc(g)}</tag>")
    if info.get("webpage_url"):
        lines.append(f"  <trailer>{_esc(info['webpage_url'])}</trailer>")
    lines.append(f"  <thumb>{_esc(video_file.stem)}.poster.jpg</thumb>")
    lines.append("  <fanart>")
    lines.append(f"    <thumb>{_esc(video_file.stem)}.fanart.jpg</thumb>")
    lines.append("  </fanart>")
    lines.append("</movie>")
    return "\n".join(lines) + "\n"


def write_nfo(video_file: Path, info: dict[str, Any]) -> Path | None:
    nfo_path = video_file.with_suffix(".nfo")
    nfo_path.write_text(build_nfo(info, video_file), encoding="utf-8")
    log.info("Wrote NFO: %s", nfo_path)
    return nfo_path


def _download_image(url: str, dest: Path, timeout: float = 30.0) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "yt2jellyfin/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 500:
            log.warning("Thumbnail too small (%d bytes), skipping: %s", len(data), url)
            return False
        dest.write_bytes(data)
        log.info("Wrote image: %s", dest)
        return True
    except Exception as exc:  # noqa: BLE001 — network best-effort
        log.warning("Failed to download thumbnail %s: %s", url, exc)
        return False


def write_artwork(video_file: Path, info: dict[str, Any], *, poster: bool, fanart: bool) -> None:
    if not poster and not fanart:
        return
    urls = _thumb_candidates(info)
    if not urls:
        log.info("No thumbnail URL in metadata for %s", video_file)
        return
    stem = video_file.stem
    if poster:
        dest = video_file.with_name(f"{stem}.poster.jpg")
        if not dest.exists():
            for u in urls:
                if _download_image(u, dest):
                    break
    if fanart:
        dest = video_file.with_name(f"{stem}.fanart.jpg")
        if not dest.exists():
            # fanart: last (often largest) candidate different from poster
            for u in reversed(urls):
                if _download_image(u, dest):
                    break
