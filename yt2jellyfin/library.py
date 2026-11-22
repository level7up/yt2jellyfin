from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize(name: str, max_len: int = 150) -> str:
    """Make a string safe for Windows/macOS/Linux filenames."""
    name = unicodedata.normalize("NFC", name).strip().rstrip(".")
    name = _INVALID.sub("_", name)
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = "untitled"
    stem = name.split(".")[0].upper()
    if stem in _WINDOWS_RESERVED:
        name = f"_{name}"
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return name


def video_dir_name(title: str, video_id: str, year: int | str | None = None) -> str:
    base = sanitize(title)
    if year:
        return f"{base} ({year})"
    return f"{base} [{video_id}]" if video_id else base


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
