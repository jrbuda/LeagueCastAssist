from __future__ import annotations

from pathlib import Path

from league_cast_assist.config import AppSettings, media_dir


def media_root(settings: AppSettings | None = None) -> Path:
    """Root that ``song.file_path`` and ``song.artwork_path`` are relative to."""
    override = (settings.music.media_dir if settings is not None else "") or ""
    if override.strip():
        return Path(override.strip()).expanduser()
    return media_dir()


def resolve_media(root: Path, relative_path: str | None) -> Path | None:
    if not relative_path:
        return None
    return root / relative_path.replace("\\", "/")


def media_available(root: Path) -> bool:
    return (root / "music").is_dir() or (root / "artwork").is_dir()
