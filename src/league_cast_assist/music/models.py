from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

PlaybackMode = Literal["shuffle", "manual", "disabled"]
SUPPORTED_AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}


class Song(BaseModel):
    """One library row. ``file_path``/``artwork_path`` are media-root relative."""

    song_id: int
    file_path: str
    title: str
    artist: str = ""
    album: str | None = None
    album_artist: str | None = None
    artwork_path: str | None = None
    source_url: str | None = None
    duration_seconds: float = 0.0
    playback_mode: PlaybackMode = "shuffle"
    group_name: str | None = None
    metadata_verified: bool = False

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Song:
        keys = row.keys()
        verified = bool(row["metadata_verified"]) if "metadata_verified" in keys else False
        return cls(
            song_id=int(row["id"]),
            file_path=row["file_path"],
            title=row["title"],
            artist=row["artist"] or "",
            album=row["album"],
            album_artist=row["album_artist"] if "album_artist" in keys else None,
            artwork_path=row["artwork_path"],
            source_url=row["source_url"] if "source_url" in keys else None,
            duration_seconds=float(row["duration_seconds"] or 0.0),
            playback_mode=row["playback_mode"] or "shuffle",
            group_name=row["group_name"] if "group_name" in keys else None,
            metadata_verified=verified,
        )

    @property
    def search_text(self) -> str:
        return " ".join(
            part for part in (self.title, self.artist, self.album or "") if part
        ).casefold()


class MediaTrack(BaseModel):
    """One file described by the central manifest."""

    file: str
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    artwork: str | None = None
    song_id: int | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = None
    artwork_sha256: str | None = None
    duration_seconds: float | None = None
    playback_mode: PlaybackMode = "shuffle"
    group_name: str | None = None
    source_url: str | None = None

    @field_validator("file", "artwork")
    @classmethod
    def _safe_relative(cls, value: str | None) -> str | None:
        if value is None:
            return None
        raw = value.replace("\\", "/")
        if raw.startswith("/") or ":" in raw:
            raise ValueError(f"absolute media path not allowed: {value}")
        normalized = raw.strip()
        parts = normalized.split("/")
        if not normalized or any(part in {"", ".", ".."} for part in parts):
            raise ValueError(f"unsafe media path: {value}")
        return normalized

    @field_validator("sha256", "artwork_sha256")
    @classmethod
    def _hex_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip().lower()
        if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
            raise ValueError("sha256 must be 64 hex characters")
        return text

    @property
    def suffix(self) -> str:
        return Path(self.file).suffix.lower()


class MediaManifest(BaseModel):
    schema_version: int = 1
    library_version: str = ""
    generated_at: str = ""
    min_app_version: str | None = None
    tracks: list[MediaTrack] = Field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(track.size_bytes or 0 for track in self.tracks)
