#!/usr/bin/env python3
"""Download a portable Windows ffmpeg essentials build into vendor/ffmpeg/."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# gyan.dev essentials build — much smaller than "full" (~70 MB vs ~440 MB).
DEFAULT_URL = (
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
)


def _extract_bin(zip_path: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    wanted = {"ffmpeg.exe", "ffprobe.exe"}
    found: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = Path(info.filename).name.lower()
            if name in wanted and not info.is_dir():
                target = dest / Path(info.filename).name
                with zf.open(info) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)
                found.append(target)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument(
        "--dest",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "vendor" / "ffmpeg",
    )
    ap.add_argument("--force", action="store_true", help="Re-download even if present")
    args = ap.parse_args()

    dest: Path = args.dest
    ffmpeg = dest / "ffmpeg.exe"
    ffprobe = dest / "ffprobe.exe"
    if not args.force and ffmpeg.is_file() and ffprobe.is_file():
        print(f"ffmpeg already present: {dest}")
        return 0

    print(f"Downloading {args.url} …")
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "ffmpeg.zip"
        urllib.request.urlretrieve(args.url, zip_path)  # noqa: S310 — fixed URL
        print(f"Extracting ffmpeg/ffprobe -> {dest}")
        found = _extract_bin(zip_path, dest)

    if not (ffmpeg.is_file() and ffprobe.is_file()):
        print(f"error: expected ffmpeg.exe + ffprobe.exe in {dest}, got {found}", file=sys.stderr)
        return 1
    print(f"OK: {ffmpeg} ({ffmpeg.stat().st_size // 1_000_000} MB)")
    print(f"OK: {ffprobe} ({ffprobe.stat().st_size // 1_000_000} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
