"""Central media catalog client for ``lca.budaslounge.com``.

The server publishes a manifest (what the library should contain) and the bytes.
The client compares the manifest against the local media folder on every launch
and on demand, downloads only what differs, and quarantines local files that the
server no longer lists (never deletes them: a bad manifest push must not be able
to destroy a producer's library mid-stream).

An empty ``base_url`` keeps the whole feature inert: no network calls.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from league_cast_assist.config import CatalogSettings
from league_cast_assist.music.models import MediaManifest, MediaTrack

LOGGER = logging.getLogger(__name__)

CHUNK_SIZE = 256 * 1024
TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)

AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
ARTWORK_SUFFIXES = {".jpg", ".jpeg", ".png"}
ORPHAN_DIR = "_orphaned"

ProgressCallback = Callable[[str, int, int], None]
CancelCallback = Callable[[], bool]

MANIFEST_HINT = (
    "No media server is configured yet. Tracks and artwork can still be placed "
    "manually in the media folder."
)

MANIFEST_HINT = (
    "No media server is configured yet. Tracks and artwork can still be placed "
    "manually in the media folder."
)


class CatalogError(RuntimeError):
    pass


def client_version() -> str:
    try:
        from league_cast_assist import __version__  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - version is cosmetic here
        return "dev"
    return str(__version__)


@dataclass
class SyncReport:
    downloaded: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    bytes_downloaded: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def changed(self) -> bool:
        return bool(self.downloaded or self.orphaned or self.failed)

    def summary(self) -> str:
        parts = [f"{self.downloaded} downloaded", f"{self.skipped} up to date"]
        if self.orphaned:
            parts.append(f"{len(self.orphaned)} quarantined")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return ", ".join(parts)


class MediaCatalog:
    def __init__(self, catalog: CatalogSettings, media_root: Path) -> None:
        self._catalog = catalog
        self._media_root = media_root

    # ── configuration ──────────────────────────────────────────────────────
    @property
    def configured(self) -> bool:
        return bool(self._catalog.base_url.strip())

    def _base(self) -> str:
        base = self._catalog.base_url.strip().rstrip("/")
        if not base:
            raise CatalogError(MANIFEST_HINT)
        return base

    def manifest_url(self) -> str:
        manifest = (self._catalog.manifest or "music/manifest.json").lstrip("/")
        return f"{self._base()}/{manifest}"

    def media_url(self, relative_path: str) -> str:
        prefix = (self._catalog.media_prefix or "media").strip("/")
        return f"{self._base()}/{prefix}/{relative_path.lstrip('/')}"

    def health_url(self) -> str:
        return f"{self._base()}/health"

    def _headers(self) -> dict[str, str]:
        headers = {"X-LCA-Client": f"lca/{client_version()}"}
        token = self._catalog.token.strip()
        if token:
            headers["X-LCA-Token"] = token
        return headers

    # ── server info ────────────────────────────────────────────────────────
    async def health(self) -> dict:
        """Cheap probe used by the settings dialog before a sync."""
        url = self.health_url()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise CatalogError(f"Could not reach the media server: {exc}") from exc
        except ValueError as exc:
            raise CatalogError(f"Media server health response is not JSON: {exc}") from exc

    # ── manifest ───────────────────────────────────────────────────────────
    async def fetch_manifest(self) -> MediaManifest:
        url = self.manifest_url()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                response = await client.get(url, headers=self._headers())
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise CatalogError(self._status_message(exc.response.status_code, url)) from exc
        except httpx.HTTPError as exc:
            raise CatalogError(f"Could not reach the media server: {exc}") from exc
        except ValueError as exc:
            raise CatalogError(f"Media manifest is not valid JSON: {exc}") from exc

        try:
            return MediaManifest.model_validate(payload)
        except ValueError as exc:
            raise CatalogError(f"Media manifest failed validation: {exc}") from exc

    @staticmethod
    def _status_message(status: int, url: str) -> str:
        if status == 401:
            return (
                "The media server rejected this copy's access token. Update "
                "LeagueCastAssist, or paste the current token in Settings → "
                "Media Server."
            )
        if status == 403:
            return (
                "The media server is limiting this connection (too many requests "
                "or simultaneous downloads). Wait a minute and try again."
            )
        if status == 404:
            return f"The media manifest is missing on the server ({url})."
        return f"Media server returned {status} for {url}"

    # ── download ───────────────────────────────────────────────────────────
    async def sync(
        self,
        progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
        verify_only: bool = False,
        prune_orphans: bool = True,
    ) -> SyncReport:
        manifest = await self.fetch_manifest()
        report = SyncReport()

        jobs: list[tuple[str, str | None, int | None]] = []
        for track in manifest.tracks:
            jobs.append((track.file, track.sha256, track.size_bytes))
            if track.artwork:
                jobs.append((track.artwork, track.artwork_sha256, None))

        total_files = len(jobs)
        for index, (relative, sha, size) in enumerate(jobs, start=1):
            if cancel is not None and cancel():
                raise CatalogError("Media download cancelled")
            target = self._media_root / relative
            label = f"{index}/{total_files} · {Path(relative).name}"
            if target.is_file() and self._file_matches(target, sha, size):
                report.skipped += 1
                self._report(progress, f"Verified {label}", index, total_files)
                continue
            if verify_only:
                report.failed.append(f"{relative}: missing or corrupted")
                continue
            try:
                written = await self._download(relative, target, progress, label,
                                               index, total_files, cancel)
                report.downloaded += 1
                report.bytes_downloaded += written
            except CatalogError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop the sync
                LOGGER.warning("Failed to download %s", relative, exc_info=True)
                report.failed.append(f"{relative}: {exc}")

        if prune_orphans and not verify_only:
            report.orphaned = self._quarantine_orphans(manifest, progress)

        self._report(progress, "Media sync complete", total_files, total_files)
        return report

    def _quarantine_orphans(
        self,
        manifest: MediaManifest,
        progress: ProgressCallback | None = None,
    ) -> list[str]:
        """Move local files the server no longer lists into ``_orphaned/``.

        Nothing is deleted: the bytes stay on disk, one folder over, and the
        library rows are disabled by the caller so the picker stops offering
        them. That makes "track removed on the server" safe and reversible.
        """
        expected: set[str] = set()
        for track in manifest.tracks:
            expected.add(track.file.replace("\\", "/"))
            if track.artwork:
                expected.add(track.artwork.replace("\\", "/"))

        orphaned: list[str] = []
        media_root = self._media_root
        if not media_root.is_dir():
            return orphaned

        for path in sorted(media_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(media_root).as_posix()
            if relative.startswith(f"{ORPHAN_DIR}/"):
                continue
            if path.suffix.lower() not in AUDIO_SUFFIXES | ARTWORK_SUFFIXES:
                continue
            if relative in expected:
                continue
            destination = media_root / ORPHAN_DIR / relative
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(destination))
            except OSError:
                LOGGER.warning("Could not quarantine %s", relative, exc_info=True)
                continue
            orphaned.append(relative)
            LOGGER.info("Quarantined %s -> %s", relative, destination)
            if progress is not None:
                progress(f"Quarantined {Path(relative).name}", 0, 0)

        return orphaned

    async def _download(
        self,
        relative: str,
        target: Path,
        progress: ProgressCallback | None,
        label: str,
        index: int,
        total: int,
        cancel: CancelCallback | None,
    ) -> int:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".part")
        written = 0
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                async with client.stream(
                    "GET", self.media_url(relative), headers=self._headers()
                ) as response:
                    if response.status_code in {401, 403}:
                        raise CatalogError(self._status_message(response.status_code, relative))
                    response.raise_for_status()
                    with temp.open("wb") as handle:
                        async for chunk in response.aiter_bytes(CHUNK_SIZE):
                            if cancel is not None and cancel():
                                raise CatalogError("Media download cancelled")
                            handle.write(chunk)
                            written += len(chunk)
                            self._report(
                                progress,
                                f"Downloading {label}",
                                index - 1,
                                total,
                                written,
                            )
            temp.replace(target)
            return written
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    def _file_matches(self, target: Path, sha: str | None, size: int | None) -> bool:
        try:
            if size is not None and target.stat().st_size != size:
                return False
            if sha:
                return self._sha256(target) == sha
        except OSError:
            return False
        return True

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _report(
        progress: ProgressCallback | None,
        message: str,
        current: int,
        total: int,
        extra: int = 0,
    ) -> None:
        if progress is not None:
            progress(message, current, total + (1 if extra and total else 0))


def manifest_to_rows(manifest: MediaManifest) -> list[dict]:
    """Flatten manifest tracks into library upsert rows."""
    return [
        {
            "song_id": track.song_id,
            "file": track.file,
            "title": track.title,
            "artist": track.artist,
            "album": track.album,
            "album_artist": track.album_artist,
            "artwork": track.artwork,
            "source_url": track.source_url,
            "duration_seconds": track.duration_seconds,
            "playback_mode": track.playback_mode,
            "group_name": track.group_name,
        }
        for track in manifest.tracks
    ]


def required_files(manifest: MediaManifest) -> list[MediaTrack]:
    return list(manifest.tracks)
