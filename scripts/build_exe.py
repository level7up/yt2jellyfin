#!/usr/bin/env python3
"""Build standalone Windows exe: fetch ffmpeg → PyInstaller → dist/yt2jellyfin/."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)


def ensure_pillow_ico() -> None:
    """Best-effort: convert icon128.png → assets/icon.ico for a proper exe icon."""
    png = ROOT / "extension" / "icons" / "icon128.png"
    ico = ROOT / "assets" / "icon.ico"
    if not png.is_file() or ico.is_file():
        return
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed — skipping .ico (exe will use default icon)")
        return
    ico.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(png).convert("RGBA")
    img.save(ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128)])
    print(f"Wrote {ico}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-ffmpeg", action="store_true", help="Do not re-fetch vendor/ffmpeg")
    ap.add_argument("--clean", action="store_true", help="Remove build/ and dist/ first")
    args = ap.parse_args()

    if args.clean:
        for d in ("build", "dist"):
            p = ROOT / d
            if p.exists():
                shutil.rmtree(p)
                print(f"Removed {p}")

    # 1. deps
    run([sys.executable, "-m", "pip", "install", "-e", "."])
    run([sys.executable, "-m", "pip", "install", "pyinstaller", "pywebview", "pillow"])

    # 2. ffmpeg (portable essentials)
    if not args.skip_ffmpeg:
        run([sys.executable, str(ROOT / "scripts" / "fetch_ffmpeg.py")])

    # 3. icon
    ensure_pillow_ico()

    # 4. PyInstaller
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(ROOT / "yt2jellyfin.spec")])

    dist = ROOT / "dist" / "yt2jellyfin"
    exe = dist / "yt2jellyfin.exe"
    if not exe.is_file():
        print(f"error: missing {exe}", file=sys.stderr)
        return 1

    size_mb = sum(f.stat().st_size for f in dist.rglob("*") if f.is_file()) / 1_000_000
    print()
    print(f"✅ Built {exe}  ({size_mb:.0f} MB folder)")
    print("   Zip the dist/yt2jellyfin folder for GitHub Releases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
