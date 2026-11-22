from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_NAMES = ("config.yaml", "config.yml")


def find_config(explicit: str | Path | None = None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"Config not found: {p}")
        return p
    env = os.environ.get("YT2JELLYFIN_CONFIG")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
    here = Path.cwd()
    for base in (here, *here.parents):
        for name in DEFAULT_CONFIG_NAMES:
            cand = base / name
            if cand.is_file():
                return cand
        if (base / "yt2jellyfin").is_dir():
            break
    pkg_root = Path(__file__).resolve().parent.parent
    for name in DEFAULT_CONFIG_NAMES:
        cand = pkg_root / name
        if cand.is_file():
            return cand
    return None


@dataclass
class QualityConfig:
    max_height: int | None = 1080
    format: str | None = None


@dataclass
class SubtitleConfig:
    enabled: bool = True
    langs: list[str] = field(default_factory=lambda: ["en"])
    embed: bool = True
    sidecar: bool = False


@dataclass
class MetadataConfig:
    nfo: bool = True
    poster: bool = True
    fanart: bool = True


@dataclass
class DownloadConfig:
    concurrent: int = 1
    retries: int = 3
    retry_delay: float = 30.0
    playlist_items: str | None = None
    cookies_from_browser: str | None = None
    cookiefile: str | None = None
    sleep: float | None = None


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8392


@dataclass
class Config:
    library_path: Path
    download_archive: Path | None = None
    quality: QualityConfig = field(default_factory=QualityConfig)
    subtitles: SubtitleConfig = field(default_factory=SubtitleConfig)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    web: WebConfig = field(default_factory=WebConfig)
    config_path: Path | None = None
    state_dir: Path | None = None
    # library_path from config.yaml — used to detect per-job dest overrides
    default_library_path: Path | None = None

    @property
    def resolved_state_dir(self) -> Path:
        if self.state_dir:
            return self.state_dir
        if self.config_path:
            return self.config_path.parent / ".yt2jellyfin"
        return Path.cwd() / ".yt2jellyfin"

    @property
    def queue_db(self) -> Path:
        return self.resolved_state_dir / "queue.db"


def _as_path(value: str | Path | None, base: Path | None) -> Path | None:
    if value is None:
        return None
    p = Path(str(value)).expanduser()
    if not p.is_absolute() and base is not None:
        p = base / p
    return p


def load_config(explicit: str | Path | None = None) -> Config:
    path = find_config(explicit)
    data: dict[str, Any] = {}
    base: Path | None = None
    if path:
        base = path.parent
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    library = data.get("library_path")
    if not library:
        raise ValueError(
            "library_path is required in config. "
            "Copy config.example.yaml to config.yaml and set it to your Jellyfin library folder."
        )

    quality = QualityConfig(**(data.get("quality") or {}))
    subtitles = SubtitleConfig(**(data.get("subtitles") or {}))
    metadata = MetadataConfig(**(data.get("metadata") or {}))
    download = DownloadConfig(**(data.get("download") or {}))
    web = WebConfig(**(data.get("web") or {}))

    archive = _as_path(data.get("download_archive"), base)
    state_dir = _as_path(data.get("state_dir"), base)

    lib = Path(str(library)).expanduser()
    return Config(
        library_path=lib,
        default_library_path=lib,
        download_archive=archive,
        quality=quality,
        subtitles=subtitles,
        metadata=metadata,
        download=download,
        web=web,
        config_path=path,
        state_dir=state_dir,
    )
