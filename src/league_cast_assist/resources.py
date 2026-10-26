from __future__ import annotations

import sys
from pathlib import Path


def resource_path(relative_path: str) -> Path:
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root) / relative_path
    return Path(__file__).resolve().parents[2] / relative_path


def app_icon_path() -> Path:
    return resource_path("megaphone-icon.png")


def seed_music_db_path() -> Path:
    """Packaged seed library copied beside the exe on first run."""
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root) / "music.db"
    return Path(__file__).resolve().parent / "music" / "seed" / "music.db"
