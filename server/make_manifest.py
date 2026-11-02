"""Generate manifest.json for the lca media server.

Walks the media tree, hashes every file, and pulls display metadata from the
seed music.db when it is available so the server and the shipped library agree.

    python3 make_manifest.py --media-root lca-media --db music.db --out manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
ARTWORK_SUFFIXES = {".jpg", ".jpeg", ".png"}
SCHEMA_VERSION = 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_metadata(db_path: Path | None) -> dict[str, dict]:
    if db_path is None or not db_path.is_file():
        return {}
    metadata: dict[str, dict] = {}
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, file_path, title, artist, album, album_artist, artwork_path,"
            " source_url, duration_seconds, playback_mode, group_name FROM songs"
        )
        for row in rows:
            metadata[str(row["file_path"]).replace("\\", "/")] = dict(row)
        connection.close()
    except sqlite3.Error as error:  # pragma: no cover - ops convenience
        print(f"warning: could not read {db_path}: {error}", file=sys.stderr)
    return metadata


def build_track(audio: Path, media_root: Path, metadata: dict[str, dict]) -> dict:
    relative = audio.relative_to(media_root).as_posix()
    row = metadata.get(relative, {})
    artwork_relative = row.get("artwork_path") or None
    track: dict = {
        "file": relative,
        "size_bytes": audio.stat().st_size,
        "sha256": sha256_file(audio),
        "title": row.get("title") or audio.stem,
        "artist": row.get("artist") or "",
        "album": row.get("album"),
        "album_artist": row.get("album_artist"),
        "duration_seconds": float(row.get("duration_seconds") or 0.0) or None,
        "playback_mode": row.get("playback_mode") or "shuffle",
        "group_name": row.get("group_name"),
        "source_url": row.get("source_url"),
    }
    if row.get("id") is not None:
        track["song_id"] = int(row["id"])

    if artwork_relative:
        artwork_path = media_root / artwork_relative
        if artwork_path.is_file():
            track["artwork"] = artwork_relative
            track["artwork_sha256"] = sha256_file(artwork_path)
        else:
            print(f"warning: artwork missing for {relative}: {artwork_relative}", file=sys.stderr)
    return track


def main() -> int:
    parser = argparse.ArgumentParser(description="Build manifest.json for the lca media server")
    parser.add_argument("--media-root", default="lca-media", type=Path)
    parser.add_argument("--db", default=None, type=Path, help="seed music.db for display metadata")
    parser.add_argument("--out", default="manifest.json", type=Path)
    parser.add_argument("--library-version", default=None)
    parser.add_argument("--min-app-version", default=None)
    args = parser.parse_args()

    media_root = args.media_root.resolve()
    if not media_root.is_dir():
        print(f"error: media root not found: {media_root}", file=sys.stderr)
        return 1

    metadata = load_metadata(args.db)
    tracks = []
    audio_files = sorted(
        path for path in media_root.rglob("*")
        if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
    )
    for audio in audio_files:
        tracks.append(build_track(audio, media_root, metadata))

    library_version = args.library_version or datetime.now(timezone.utc).strftime("%Y.%m.%d")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "library_version": library_version,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "tracks_total": len(tracks),
        "bytes_total": sum(track["size_bytes"] for track in tracks),
        "tracks": tracks,
    }
    if args.min_app_version:
        manifest["min_app_version"] = args.min_app_version

    args.out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    matched = sum(1 for track in tracks if track.get("song_id") is not None)
    print(
        f"wrote {args.out}: {len(tracks)} tracks, "
        f"{manifest['bytes_total'] / 1_000_000:.1f} MB, "
        f"metadata matched {matched}/{len(tracks)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

