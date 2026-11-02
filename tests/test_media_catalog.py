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
    catalog = MediaCatalog(CatalogSettings(base_url=""), tmp_path)
    assert catalog.configured is False
    with pytest.raises(CatalogError) as excinfo:
        catalog.manifest_url()
    assert "configured" in str(excinfo.value)


def test_defaults_point_at_the_live_server() -> None:
    settings = CatalogSettings()
    assert settings.base_url == "https://lca.budaslounge.com"
    assert settings.manifest == "music/manifest.json"
    assert settings.media_prefix == "media"
    assert settings.sync_on_launch is True
    assert settings.token.startswith("LCA-")


def test_manifest_url_joins_base_and_manifest(tmp_path: Path) -> None:
    catalog = MediaCatalog(
        CatalogSettings(base_url="https://lca.budaslounge.com/"), tmp_path
    )
    assert catalog.manifest_url() == "https://lca.budaslounge.com/music/manifest.json"
    assert catalog.media_url("music/a.mp3") == "https://lca.budaslounge.com/media/music/a.mp3"
    assert catalog.health_url() == "https://lca.budaslounge.com/health"


# ── download ───────────────────────────────────────────────────────────────
@pytest.fixture()
def media_server(tmp_path: Path):
    root = tmp_path / "server"
    (root / "music").mkdir(parents=True)
    (root / "media" / "music").mkdir(parents=True)
    (root / "media" / "artwork").mkdir(parents=True)
    track_bytes = b"ID3fake-mp3-payload" * 10
    art_bytes = b"\xff\xd8\xff-jpeg-payload"
    (root / "media" / "music" / "One.mp3").write_bytes(track_bytes)
    (root / "media" / "artwork" / "one.jpg").write_bytes(art_bytes)

    manifest = {
        "schema_version": 1,
        "library_version": "2026.11.02",
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
    (root / "music" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

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
    seen_headers: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802
        _QuietHandler.seen_headers = {k.lower(): v for k, v in self.headers.items()}
        super().do_GET()

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


def test_token_and_client_headers_are_sent(media_server: str, tmp_path: Path) -> None:
    catalog = MediaCatalog(
        CatalogSettings(base_url=media_server, token="LCA-test-token"), tmp_path
    )
    asyncio.run(catalog.fetch_manifest())
    assert _QuietHandler.seen_headers.get("x-lca-token") == "LCA-test-token"
    assert _QuietHandler.seen_headers.get("x-lca-client", "").startswith("lca/")


def test_sync_quarantines_files_the_server_no_longer_lists(
    media_server: str, tmp_path: Path
) -> None:
    media_root = tmp_path / "media"
    (media_root / "music").mkdir(parents=True)
    (media_root / "artwork").mkdir(parents=True)
    stale_track = media_root / "music" / "Retired.mp3"
    stale_track.write_bytes(b"old-track")
    keep = media_root / "music" / "Keep.txt"
    keep.write_text("not media", encoding="utf-8")

    catalog = MediaCatalog(CatalogSettings(base_url=media_server), media_root)
    report = asyncio.run(catalog.sync())

    assert "music/Retired.mp3" in report.orphaned
    assert not stale_track.exists()
    assert (media_root / "_orphaned" / "music" / "Retired.mp3").read_bytes() == b"old-track"
    # Files that are not media are left alone.
    assert keep.exists()
    # The synced track is still there.
    assert (media_root / "music" / "One.mp3").is_file()


def test_quarantine_can_be_skipped(media_server: str, tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    (media_root / "music").mkdir(parents=True)
    stale = media_root / "music" / "Retired.mp3"
    stale.write_bytes(b"old")

    catalog = MediaCatalog(CatalogSettings(base_url=media_server), media_root)
    report = asyncio.run(catalog.sync(prune_orphans=False))

    assert report.orphaned == []
    assert stale.exists()
