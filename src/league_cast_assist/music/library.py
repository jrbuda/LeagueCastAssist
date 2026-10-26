"""SQLite music library: schema migration, songs, sessions and play history.

The schema is owned by the existing library that the Twitch bot and the
standalone producer share, so migrations must stay additive and idempotent.
All calls happen on the GUI thread; the connection is opened with WAL so the
external vote writer never blocks a read.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from league_cast_assist.music.models import Song
from league_cast_assist.music.selection import eligible_shuffle_songs

LOGGER = logging.getLogger(__name__)

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS songs (
        id INTEGER PRIMARY KEY,
        file_path TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        artist TEXT NOT NULL,
        album TEXT,
        album_artist TEXT,
        artwork_path TEXT NOT NULL,
        source_url TEXT,
        duration_seconds REAL NOT NULL,
        playback_mode TEXT NOT NULL
            CHECK (playback_mode IN ('shuffle', 'manual', 'disabled')),
        group_name TEXT,
        metadata_verified INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stream_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        ended_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stream_plays (
        stream_id INTEGER NOT NULL,
        song_id INTEGER NOT NULL,
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(stream_id, song_id)
    )
    """,
    # Unlike stream_plays, play_events permits repeats.
    """
    CREATE TABLE IF NOT EXISTS play_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stream_id INTEGER NOT NULL,
        song_id INTEGER NOT NULL,
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_play_events_session
    ON play_events(stream_id, id)
    """,
    # One-time import of legacy no-repeat history. Guarded so a session that
    # already has events is never duplicated.
    """
    INSERT INTO play_events(stream_id, song_id, started_at)
    SELECT old.stream_id, old.song_id, old.started_at
    FROM stream_plays AS old
    WHERE NOT EXISTS (
        SELECT 1 FROM play_events AS p WHERE p.stream_id = old.stream_id
    )
    ORDER BY old.stream_id, old.started_at, old.rowid
    """,
)


@dataclass(frozen=True)
class LibraryStats:
    used: int = 0
    total: int = 0
    plays: int = 0
    eligible: int = 0


class MusicLibrary:
    def __init__(self, db_path: Path, seed_path: Path | None = None) -> None:
        self._db_path = db_path
        self._seed_path = seed_path
        self._db: sqlite3.Connection | None = None
        self._songs: dict[int, Song] = {}
        self._session_id = 0
        self._error = ""

    # ── lifecycle ──────────────────────────────────────────────────────────
    @property
    def available(self) -> bool:
        return self._db is not None

    @property
    def error(self) -> str:
        return self._error

    @property
    def db_path(self) -> Path:
        return self._db_path

    def open(self) -> bool:
        """Create/seed the database and load songs. Never raises."""
        if self._db is not None:
            return True

        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._seed_if_missing()
            self._db = sqlite3.connect(self._db_path, timeout=5.0)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA busy_timeout=5000")
            with self._db:
                for statement in SCHEMA:
                    self._db.execute(statement)
            self._session_id = self._open_session()
            self._load_songs()
            self._error = ""
            return True
        except (sqlite3.Error, OSError) as exc:
            LOGGER.warning("Music library unavailable: %s", exc, exc_info=True)
            self._error = str(exc)
            self._close_quietly()
            return False

    def close(self) -> None:
        self._close_quietly()

    def _close_quietly(self) -> None:
        if self._db is not None:
            try:
                self._db.close()
            except sqlite3.Error:  # pragma: no cover - defensive
                LOGGER.debug("Failed to close music database", exc_info=True)
        self._db = None
        self._songs = {}
        self._session_id = 0

    def _seed_if_missing(self) -> None:
        if self._db_path.exists() or self._seed_path is None:
            return
        if not self._seed_path.is_file():
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._seed_path, self._db_path)
        LOGGER.info("Seeded music library from %s", self._seed_path)

    # ── reads ──────────────────────────────────────────────────────────────
    @property
    def songs(self) -> dict[int, Song]:
        return self._songs

    @property
    def session_id(self) -> int:
        return self._session_id

    def _load_songs(self) -> None:
        self._songs = {}
        if self._db is None:
            return
        for row in self._db.execute("SELECT * FROM songs ORDER BY title COLLATE NOCASE"):
            song = Song.from_row(row)
            self._songs[song.song_id] = song

    def reload(self) -> None:
        self._load_songs()

    def votes_by_song(self) -> dict[int, int]:
        if self._db is None:
            return {}
        try:
            rows = self._db.execute(
                "SELECT song_id, COUNT(*) AS n FROM votes GROUP BY song_id"
            )
            return {int(row["song_id"]): int(row["n"]) for row in rows}
        except sqlite3.OperationalError:
            # The Twitch bot creates this table lazily.
            return {}

    def play_history(self) -> list[int]:
        if self._db is None:
            return []
        try:
            rows = self._db.execute(
                "SELECT song_id FROM play_events WHERE stream_id=? ORDER BY id",
                (self._session_id,),
            )
            return [int(row["song_id"]) for row in rows]
        except sqlite3.Error:
            LOGGER.debug("Failed to read play history", exc_info=True)
            return []

    def stats(self) -> LibraryStats:
        history = self.play_history()
        eligible = eligible_shuffle_songs(self._songs.values(), history)
        return LibraryStats(
            used=len(set(history)),
            total=len(self._songs),
            plays=len(history),
            eligible=len(eligible),
        )

    def song(self, song_id: int) -> Song | None:
        return self._songs.get(song_id)

    # ── writes ─────────────────────────────────────────────────────────────
    def _open_session(self) -> int:
        assert self._db is not None  # noqa: S101 - internal invariant
        row = self._db.execute(
            "SELECT id FROM stream_sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            return int(row["id"])
        with self._db:
            cursor = self._db.execute("INSERT INTO stream_sessions DEFAULT VALUES")
            return int(cursor.lastrowid or 0)

    def record_play(self, song_id: int) -> None:
        if self._db is None:
            return
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO play_events(stream_id, song_id) VALUES(?, ?)",
                    (self._session_id, song_id),
                )
        except sqlite3.Error:
            LOGGER.warning("Failed to record play", exc_info=True)

    def new_stream_session(self) -> int:
        if self._db is None:
            return 0
        try:
            with self._db:
                self._db.execute(
                    "UPDATE stream_sessions SET ended_at=CURRENT_TIMESTAMP "
                    "WHERE ended_at IS NULL"
                )
                cursor = self._db.execute("INSERT INTO stream_sessions DEFAULT VALUES")
            self._session_id = int(cursor.lastrowid or 0)
        except sqlite3.Error:
            LOGGER.warning("Failed to start a new stream session", exc_info=True)
        return self._session_id

    def upsert_manifest_tracks(self, tracks: list[dict]) -> int:
        """Insert tracks described by the manifest without touching existing rows.

        Existing ids/paths keep their playback_mode, group_name and history, so a
        media sync can never rewrite the Twitch-facing library.
        """
        if self._db is None:
            return 0
        inserted = 0
        try:
            with self._db:
                for track in tracks:
                    cursor = self._db.execute(
                        """
                        INSERT INTO songs (
                            id, file_path, title, artist, album, album_artist,
                            artwork_path, source_url, duration_seconds,
                            playback_mode, group_name, metadata_verified
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(file_path) DO NOTHING
                        """,
                        (
                            track.get("song_id"),
                            track["file"],
                            track.get("title") or Path(track["file"]).stem,
                            track.get("artist") or "",
                            track.get("album"),
                            track.get("album_artist"),
                            track.get("artwork") or "",
                            track.get("source_url"),
                            float(track.get("duration_seconds") or 0.0),
                            track.get("playback_mode") or "shuffle",
                            track.get("group_name"),
                        ),
                    )
                    inserted += cursor.rowcount if cursor.rowcount > 0 else 0
        except sqlite3.Error:
            LOGGER.warning("Failed to upsert manifest tracks", exc_info=True)
        self.reload()
        return inserted
