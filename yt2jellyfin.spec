# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — standalone yt2jellyfin.exe (bundles static UI + ffmpeg)."""

from pathlib import Path

ROOT = Path(SPECPATH).resolve()  # noqa: F821 — provided by PyInstaller
VENDOR_FFMPEG = ROOT / "vendor" / "ffmpeg"
STATIC = ROOT / "yt2jellyfin" / "static"
EXAMPLE = ROOT / "config.example.yaml"
ICON_PNG = ROOT / "extension" / "icons" / "icon128.png"

datas = [(str(STATIC), "yt2jellyfin/static")]
if EXAMPLE.is_file():
    datas.append((str(EXAMPLE), "."))
if VENDOR_FFMPEG.is_dir():
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        p = VENDOR_FFMPEG / name
        if p.is_file():
            datas.append((str(p), "ffmpeg"))

# Prefer a .ico if present (build script may generate one); else PNG is fine on Win10+.
icon_file = ROOT / "assets" / "icon.ico"
icon = str(icon_file) if icon_file.is_file() else (str(ICON_PNG) if ICON_PNG.is_file() else None)

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "yt2jellyfin.desktop",
    "yt2jellyfin.web",
    "yt2jellyfin.cli",
    "yt2jellyfin.downloader",
    "yt2jellyfin.queue",
    "yt2jellyfin.metadata",
    "yt2jellyfin.library",
    "yt2jellyfin.config",
]

a = Analysis(
    [str(ROOT / "desktop_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="yt2jellyfin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="yt2jellyfin",
)
