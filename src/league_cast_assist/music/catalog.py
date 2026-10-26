"""Central media catalog client.

The media server does not exist yet, so an empty ``base_url`` keeps the whole
feature inert: no network calls, and the UI offers a clear "not configured"
message instead of a broken download button.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from league_cast_assist.config import CatalogSettings
from league_cast_assist.music.models import MediaManifest, MediaTrack

LOGGER = logging.getLogger(__name__)

CHUNK_SIZE = 256 * 1024
TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)

ProgressCallback = Callable[[str, int, int], None]
CancelCallback = Callable[[], bool]

MANIFEST_HINT = (
    "No media server is configured yet. Tracks and artwork can still be placed "
    "manually in the media folder; downloading arrives once the central domain "
    "is available."
)


class CatalogError(RuntimeError):
    pass


@dataclass
class SyncReport:
    downloaded: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    bytes_downloaded: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed


class MediaCatalog:
    def __init__(self, catalog: CatalogSettings, media_root: Path) -> None:
        self._catalog = catalog
        self._media_root = media_root

    # ── configuration ──────────────────────────────────────────────────────
    @property
    def configured(self) -> bool:
        return bool(self._catalog.base_url.strip())

    def manifest_url(self) -> str:
        base = self._catalog.base_url.strip().rstrip("/")
        if not base:
            raise CatalogError(MANIFEST_HINT)
        manifest = (self._catalog.manifest or "manifest.json").lstrip("/")
        return f"{base}/{manifest}"

    def media_url(self, relative_path: str) -> str:
        base = self._catalog.base_url.strip().rstrip("/")
        return f"{base}/{relative_path.lstrip('/')}"

    # ── manifest ───────────────────────────────────────────────────────────
    async def fetch_manifest(self) -> MediaManifest:
        url = self.manifest_url()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise CatalogError(
                f"Media server returned {exc.response.status_code} for {url}"
            ) from exc
        except httpx.HTTPError as exc:
            raise CatalogError(f"Could not reach the media server: {exc}") from exc
        except ValueError as exc:
            raise CatalogError(f"Media manifest is not valid JSON: {exc}") from exc

        try:
            return MediaManifest.model_validate(payload)
        except ValueError as exc:
            raise CatalogError(f"Media manifest failed validation: {exc}") from exc

    # ── download ───────────────────────────────────────────────────────────
    async def sync(
        self,
        progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
        verify_only: bool = False,
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

        self._report(progress, "Media sync complete", total_files, total_files)
        return report

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
                async with client.stream("GET", self.media_url(relative)) as response:
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
