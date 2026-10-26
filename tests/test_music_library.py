from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from league_cast_assist.music.library import MusicLibrary


def track(song_id: int, name: str, mode: str = "shuffle") -> dict:
    return {
        "song_id": song_id,
        "file": f"music/{name}.mp3",
        "title": name,
        "artist": "Artist",
        "album": None,
        "album_artist": None,
        "artwork": f"artwork/{name}.jpg",
        "source_url": None,
        "duration_seconds": 120.0,
        "playback_mode": mode,
        "group_name": None,
    }


@pytest.fixture()
def library(tmp_path: Path) -> MusicLibrary:
    lib = MusicLibrary(tmp_path / "music.db")
    assert lib.open() is True
    yield lib
    lib.close()


def test_open_creates_schema_and_a_session(tmp_path: Path) -> None:
    lib = MusicLibrary(tmp_path / "music.db")
    assert lib.open() is True
    assert lib.session_id > 0
    tables = {
        row[0]
        for row in sqlite3.connect(tmp_path / "music.db").execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"songs", "stream_sessions", "stream_plays", "play_events"} <= tables
    lib.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "music.db"
    first = MusicLibrary(path)
    assert first.open() is True
    first.upsert_manifest_tracks([track(1, "One")])
    first.close()

    second = MusicLibrary(path)
    assert second.open() is True
    assert len(second.songs) == 1
    assert second.session_id >= 1
    second.close()


def test_upsert_does_not_overwrite_existing_rows(library: MusicLibrary) -> None:
    library.upsert_manifest_tracks([track(1, "One", mode="manual")])
    library.upsert_manifest_tracks([track(1, "One", mode="shuffle")])
    assert library.songs[1].playback_mode == "manual"


def test_missing_votes_table_is_tolerated(library: MusicLibrary) -> None:
    assert library.votes_by_song() == {}


def test_votes_are_read_when_the_bot_created_the_table(library: MusicLibrary) -> None:
    library.upsert_manifest_tracks([track(1, "One")])
    db = sqlite3.connect(library.db_path)
    db.execute("CREATE TABLE votes (song_id INTEGER, user TEXT)")
    db.execute("INSERT INTO votes VALUES (1, 'a')")
    db.execute("INSERT INTO votes VALUES (1, 'b')")
    db.commit()
    db.close()
    assert library.votes_by_song() == {1: 2}


def test_play_history_and_stats(library: MusicLibrary) -> None:
    library.upsert_manifest_tracks([track(1, "One"), track(2, "Two")])
    library.record_play(1)
    library.record_play(2)
    library.record_play(1)

    assert library.play_history() == [1, 2, 1]
    stats = library.stats()
    assert stats.total == 2
    assert stats.plays == 3
    assert stats.used == 2
    assert stats.eligible == 0  # both are inside the five-song cooldown


def test_new_stream_resets_history_but_keeps_songs(library: MusicLibrary) -> None:
    library.upsert_manifest_tracks([track(1, "One")])
    library.record_play(1)
    previous = library.session_id
    current = library.new_stream_session()
    assert current != previous
    assert library.play_history() == []
    assert len(library.songs) == 1


def test_unavailable_library_reports_an_error_instead_of_raising(tmp_path: Path) -> None:
    blocked = tmp_path / "folder"
    blocked.mkdir()
    (blocked / "music.db").mkdir()  # a directory where a file is expected
    lib = MusicLibrary(blocked / "music.db")
    assert lib.open() is False
    assert lib.available is False
    assert lib.error
    assert lib.songs == {}
    assert lib.play_history() == []
    assert lib.stats().total == 0


def test_seed_library_is_copied_on_first_open(tmp_path: Path) -> None:
    seed = tmp_path / "seed.db"
    original = MusicLibrary(seed)
    assert original.open() is True
    original.upsert_manifest_tracks([track(7, "Seeded")])
    original.close()

    target = tmp_path / "nested" / "music.db"
    lib = MusicLibrary(target, seed_path=seed)
    assert lib.open() is True
    assert 7 in lib.songs
    lib.close()

    # Re-opening must not overwrite the user's copy.
    added = MusicLibrary(target, seed_path=seed)
    assert added.open() is True
    added.upsert_manifest_tracks([track(8, "User")])
    added.close()
    again = MusicLibrary(target, seed_path=seed)
    assert again.open() is True
    assert set(again.songs) == {7, 8}
    again.close()
