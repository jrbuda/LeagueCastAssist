from __future__ import annotations

import asyncio
import functools
import hashlib
import http.server
import json
import threading
from pathlib import Path

import pytest

from league_cast_assist.config import CatalogSettings
from league_cast_assist.music.catalog import CatalogError, MediaCatalog, manifest_to_rows
from league_cast_assist.music.models import MediaManifest, MediaTrack


# ── manifest validation ────────────────────────────────────────────────────
def test_manifest_accepts_the_documented_shape() -> None:
    manifest = MediaManifest.model_validate(
        {
            "schema_version": 1,
            "library_version": "2026.10.25",
            "tracks": [
                {
                    "song_id": 1001,
                    "file": "music/Bit Rush.mp3",
                    "artwork": "artwork/arcade.jpg",
                    "sha256": "a" * 64,
                    "size_bytes": 10,
                    "title": "Bit Rush",
                    "playback_mode": "shuffle",
                }
            ],
        }
    )
    assert manifest.tracks[0].suffix == ".mp3"
    assert manifest.total_bytes == 10


@pytest.mark.parametrize(
    "bad_path",
    ["../escape.mp3", "/absolute.mp3", "music/../../x.mp3", "music\\..\\x.mp3", ""],
)
def test_manifest_rejects_unsafe_paths(bad_path: str) -> None:
    with pytest.raises(ValueError):
        MediaTrack.model_validate({"file": bad_path})


def test_manifest_rejects_malformed_digests() -> None:
    with pytest.raises(ValueError):
        MediaTrack.model_validate({"file": "music/a.mp3", "sha256": "not-a-digest"})


def test_manifest_rows_map_into_library_rows() -> None:
    manifest = MediaManifest.model_validate(
        {
            "tracks": [
                {
                    "song_id": 5,
                    "file": "music/A.mp3",
                    "artwork": "artwork/a.jpg",
                    "title": "A",
                    "artist": "B",
                    "duration_seconds": 12.5,
                    "group_name": "echoes",
                    "playback_mode": "manual",
                }
            ]
        }
    )
    rows = manifest_to_rows(manifest)
    assert rows[0]["file"] == "music/A.mp3"
    assert rows[0]["group_name"] == "echoes"
    assert rows[0]["playback_mode"] == "manual"


# ── configuration ──────────────────────────────────────────────────────────
def test_empty_base_url_keeps_the_feature_inert(tmp_path: Path) -> None:
    catalog = MediaCatalog(CatalogSettings(), tmp_path)
    assert catalog.configured is False
    with pytest.raises(CatalogError) as excinfo:
        catalog.manifest_url()
    assert "configured" in str(excinfo.value)


def test_manifest_url_joins_base_and_manifest(tmp_path: Path) -> None:
    catalog = MediaCatalog(
        CatalogSettings(base_url="https://media.example.com/music/"), tmp_path
    )
    assert catalog.manifest_url() == "https://media.example.com/music/manifest.json"
    assert catalog.media_url("music/a.mp3") == "https://media.example.com/music/music/a.mp3"


# ── download ───────────────────────────────────────────────────────────────
@pytest.fixture()
def media_server(tmp_path: Path):
    root = tmp_path / "server"
    (root / "music").mkdir(parents=True)
    (root / "artwork").mkdir(parents=True)
    track_bytes = b"ID3fake-mp3-payload" * 10
    art_bytes = b"\xff\xd8\xff-jpeg-payload"
    (root / "music" / "One.mp3").write_bytes(track_bytes)
    (root / "artwork" / "one.jpg").write_bytes(art_bytes)

    manifest = {
        "schema_version": 1,
        "tracks": [
            {
                "song_id": 1,
                "file": "music/One.mp3",
                "artwork": "artwork/one.jpg",
                "title": "One",
                "size_bytes": len(track_bytes),
                "sha256": hashlib.sha256(track_bytes).hexdigest(),
                "artwork_sha256": hashlib.sha256(art_bytes).hexdigest(),
            }
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    handler = functools.partial(_QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: ANN002
        return


def test_sync_downloads_files_and_is_idempotent(media_server: str, tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    catalog = MediaCatalog(CatalogSettings(base_url=media_server), media_root)

    report = asyncio.run(catalog.sync())
    assert report.downloaded == 2
    assert report.failed == []
    assert (media_root / "music" / "One.mp3").is_file()
    assert (media_root / "artwork" / "one.jpg").is_file()

    second = asyncio.run(catalog.sync())
    assert second.downloaded == 0
    assert second.skipped == 2
    assert second.failed == []


def test_sync_replaces_a_corrupted_file(media_server: str, tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    (media_root / "music").mkdir(parents=True)
    (media_root / "music" / "One.mp3").write_bytes(b"corrupted")
    catalog = MediaCatalog(CatalogSettings(base_url=media_server), media_root)

    report = asyncio.run(catalog.sync())
    assert report.downloaded == 2
    assert (media_root / "music" / "One.mp3").read_bytes().startswith(b"ID3")


def test_verify_only_reports_missing_files(media_server: str, tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    catalog = MediaCatalog(CatalogSettings(base_url=media_server), media_root)

    report = asyncio.run(catalog.sync(verify_only=True))
    assert report.downloaded == 0
    assert len(report.failed) == 2
    assert not (media_root / "music" / "One.mp3").exists()


def test_cancel_aborts_without_leaving_part_files(media_server: str, tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    catalog = MediaCatalog(CatalogSettings(base_url=media_server), media_root)

    with pytest.raises(CatalogError):
        asyncio.run(catalog.sync(cancel=lambda: True))
    leftovers = list(media_root.rglob("*.part")) if media_root.exists() else []
    assert leftovers == []


def test_missing_manifest_is_reported(tmp_path: Path) -> None:
    catalog = MediaCatalog(CatalogSettings(base_url="http://127.0.0.1:1"), tmp_path)
    with pytest.raises(CatalogError):
        asyncio.run(catalog.fetch_manifest())
