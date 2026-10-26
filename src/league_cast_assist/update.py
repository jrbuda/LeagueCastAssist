from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import httpx

from league_cast_assist import __version__

GITHUB_OWNER = "jrbuda"
GITHUB_REPO = "LeagueCastAssist"
APP_EXECUTABLE_NAME = "LeagueCastAssist.exe"
LATEST_RELEASE_API_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
RELEASE_BY_TAG_API_URL_TEMPLATE = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tags/{{tag}}"
)
DOWNLOAD_CHUNK_SIZE = 1024 * 1024

LOGGER = logging.getLogger(__name__)
ProgressCallback = Callable[[str, int, int], None]
CancelCallback = Callable[[], bool]


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    browser_download_url: str
    size: int
    sha256: str | None = None
    sha256_url: str | None = None


@dataclass(frozen=True)
class UpdateRelease:
    version: str
    tag_name: str
    html_url: str
    notes: str
    asset: ReleaseAsset


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str | None
    release: UpdateRelease | None
    checked_url: str
    reason: str = ""

    @property
    def update_available(self) -> bool:
        return self.release is not None


def version_key(version: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)*", version)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(0).split("."))


def normalize_release_version(version: str) -> str:
    version = version.strip()
    if version.lower().startswith("v"):
        return version[1:]
    return version


def is_newer_version(candidate_version: str, current_version: str) -> bool:
    candidate_key = version_key(candidate_version)
    current_key = version_key(current_version)
    if not candidate_key or not current_key:
        return normalize_release_version(candidate_version) != normalize_release_version(
            current_version
        )

    width = max(len(candidate_key), len(current_key))
    candidate_key = candidate_key + (0,) * (width - len(candidate_key))
    current_key = current_key + (0,) * (width - len(current_key))
    return candidate_key > current_key


def release_from_github_payload(payload: dict[str, object]) -> UpdateRelease | None:
    tag_name = str(payload.get("tag_name") or payload.get("name") or "").strip()
    if not tag_name:
        return None

    asset = _select_windows_exe_asset(payload.get("assets"))
    if asset is None:
        return None

    return UpdateRelease(
        version=normalize_release_version(tag_name),
        tag_name=tag_name,
        html_url=str(payload.get("html_url") or ""),
        notes=str(payload.get("body") or ""),
        asset=asset,
    )


class UpdateService:
    def __init__(self, latest_release_url: str = LATEST_RELEASE_API_URL) -> None:
        self._latest_release_url = latest_release_url

    async def check_latest(self, current_version: str = __version__) -> UpdateCheckResult:
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers=_github_headers(current_version),
            timeout=httpx.Timeout(20.0, connect=5.0),
        ) as client:
            response = await client.get(self._latest_release_url)

        if response.status_code == 404:
            return UpdateCheckResult(
                current_version=current_version,
                latest_version=None,
                release=None,
                checked_url=self._latest_release_url,
                reason="GitHub release information is not public yet.",
            )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise UpdateError(f"GitHub update check failed: HTTP {response.status_code}") from exc

        payload = response.json()
        tag_name = str(payload.get("tag_name") or payload.get("name") or "").strip()
        latest_version = normalize_release_version(tag_name) if tag_name else None
        if not latest_version:
            return UpdateCheckResult(
                current_version=current_version,
                latest_version=None,
                release=None,
                checked_url=self._latest_release_url,
                reason="GitHub latest release did not include a version tag.",
            )

        if not is_newer_version(latest_version, current_version):
            return UpdateCheckResult(
                current_version=current_version,
                latest_version=latest_version,
                release=None,
                checked_url=self._latest_release_url,
            )

        release = release_from_github_payload(payload)
        if release is None:
            return UpdateCheckResult(
                current_version=current_version,
                latest_version=latest_version,
                release=None,
                checked_url=self._latest_release_url,
                reason="A newer GitHub release exists, but it has no Windows exe asset.",
            )

        return UpdateCheckResult(
            current_version=current_version,
            latest_version=latest_version,
            release=release,
            checked_url=self._latest_release_url,
        )

    async def download_release(
        self,
        release: UpdateRelease,
        progress_callback: ProgressCallback | None = None,
        cancel_callback: CancelCallback | None = None,
    ) -> Path:
        updates = updates_dir()
        updates.mkdir(parents=True, exist_ok=True)
        final_path = updates / f"LeagueCastAssist-{_safe_filename(release.tag_name)}.exe"
        temp_path = final_path.with_suffix(f"{final_path.suffix}.download")

        if cancel_callback and cancel_callback():
            raise UpdateError("App update download cancelled")

        downloaded = 0
        total = release.asset.size
        if progress_callback:
            progress_callback("Downloading app update", downloaded, total)

        try:
            checksum = sha256()
            async with httpx.AsyncClient(
                follow_redirects=True,
                headers=_download_headers(),
                timeout=httpx.Timeout(120.0, connect=10.0),
            ) as client:
                expected_sha256 = await _expected_sha256(client, release.asset)
                async with client.stream("GET", release.asset.browser_download_url) as response:
                    response.raise_for_status()
                    header_length = response.headers.get("Content-Length")
                    if header_length and header_length.isdigit():
                        total = int(header_length)
                    with temp_path.open("wb") as output:
                        async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK_SIZE):
                            if cancel_callback and cancel_callback():
                                raise UpdateError("App update download cancelled")
                            checksum.update(chunk)
                            output.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback:
                                progress_callback("Downloading app update", downloaded, total)
            if expected_sha256 and checksum.hexdigest().lower() != expected_sha256.lower():
                raise UpdateError("Downloaded update failed SHA-256 verification")
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("Failed to clean up partial update download", exc_info=True)
            raise

        temp_path.replace(final_path)
        if progress_callback:
            progress_callback("App update downloaded", downloaded, total)
        return final_path


def app_dir() -> Path:
    if is_frozen_app():
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def updates_dir() -> Path:
    return app_dir() / "updates"


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def can_install_downloaded_update() -> bool:
    return sys.platform == "win32" and is_frozen_app()


def _updater_exe_path() -> Path:
    """Return path of updater.exe in the app dir, extracting from _MEIPASS when frozen."""
    dest = app_dir() / "updater.exe"
    if is_frozen_app():
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        bundled = meipass / "updater.exe"
        if bundled.exists():
            try:
                shutil.copy2(bundled, dest)
                LOGGER.debug("Extracted updater.exe from bundle to %s", dest)
            except OSError:
                LOGGER.warning("Failed to extract updater.exe from bundle", exc_info=True)
    return dest


def install_update_after_exit(download_path: Path) -> None:
    if not can_install_downloaded_update():
        raise UpdateError("Downloaded updates can only be installed from the Windows exe build.")

    source = Path(download_path).resolve()
    if not source.exists():
        raise UpdateError(f"Downloaded update not found: {source}")

    target = Path(sys.executable).resolve()
    if source == target:
        raise UpdateError("Downloaded update path matches the running executable.")

    updater = _updater_exe_path()
    if not updater.exists():
        raise UpdateError(
            f"Updater executable not found at {updater}. "
            "Rebuild the app with build.ps1 to bundle updater.exe."
        )

    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )
    subprocess.Popen(  # noqa: S603
        [
            str(updater),
            "--install",
            "--source",
            str(source),
            "--target",
            str(target),
            "--pid",
            str(os.getpid()),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )


async def fetch_release_notes(version: str) -> str:
    """Return the GitHub release body for *version*, or empty string on any failure."""
    tag = version if version.startswith("v") else f"v{version}"
    url = RELEASE_BY_TAG_API_URL_TEMPLATE.format(tag=tag)
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers=_github_headers(version),
            timeout=httpx.Timeout(15.0, connect=5.0),
        ) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return ""
            return str(response.json().get("body") or "")
    except Exception:  # noqa: BLE001
        LOGGER.warning("Failed to fetch release notes for %s", version, exc_info=True)
        return ""


def _github_headers(current_version: str) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"LeagueCastAssist/{current_version}",
    }
    token = os.getenv("LEAGUE_CAST_ASSIST_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _download_headers() -> dict[str, str]:
    headers = {"User-Agent": f"LeagueCastAssist/{__version__}"}
    token = os.getenv("LEAGUE_CAST_ASSIST_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _select_windows_exe_asset(raw_assets: object) -> ReleaseAsset | None:
    if not isinstance(raw_assets, list):
        return None

    release_assets = [asset for asset in raw_assets if isinstance(asset, dict)]
    assets: list[ReleaseAsset] = []
    for raw_asset in release_assets:
        name = str(raw_asset.get("name") or "")
        url = str(raw_asset.get("browser_download_url") or "")
        if not name.lower().endswith(".exe") or not url:
            continue
        size = raw_asset.get("size")
        checksum_url = _matching_checksum_url(name, release_assets)
        assets.append(
            ReleaseAsset(
                name=name,
                browser_download_url=url,
                size=size if isinstance(size, int) else 0,
                sha256=_asset_sha256(raw_asset.get("digest")),
                sha256_url=checksum_url,
            )
        )

    if not assets:
        return None

    return min(assets, key=_asset_sort_key)


def _asset_sort_key(asset: ReleaseAsset) -> tuple[int, int, int, str]:
    name = asset.name.lower()
    exact_name = 0 if name == APP_EXECUTABLE_NAME.lower() else 1
    release_name = 0 if "leaguecastassist" in name.replace("-", "") else 1
    debug_name = 1 if "debug" in name else 0
    return (exact_name, release_name, debug_name, name)


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-") or "update"


async def _expected_sha256(client: httpx.AsyncClient, asset: ReleaseAsset) -> str | None:
    if asset.sha256:
        return asset.sha256
    if not asset.sha256_url:
        return None
    response = await client.get(asset.sha256_url)
    response.raise_for_status()
    return _extract_sha256(response.text, asset.name)


def _asset_sha256(digest: object) -> str | None:
    if not isinstance(digest, str):
        return None
    prefix = "sha256:"
    if digest.lower().startswith(prefix):
        digest = digest[len(prefix) :]
    digest = digest.strip().lower()
    if re.fullmatch(r"[a-f0-9]{64}", digest):
        return digest
    return None


def _matching_checksum_url(asset_name: str, release_assets: list[dict[str, object]]) -> str | None:
    expected_names = {
        f"{asset_name}.sha256",
        f"{asset_name}.sha256.txt",
        "SHA256SUMS",
        "SHA256SUMS.txt",
        "checksums.txt",
    }
    for asset in release_assets:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if name in expected_names and url:
            return url
    return None


def _extract_sha256(text: str, asset_name: str) -> str | None:
    matching_lines = [line for line in text.splitlines() if asset_name in line]
    for line in matching_lines or text.splitlines():
        match = re.search(r"\b[a-fA-F0-9]{64}\b", line)
        if match:
            return match.group(0).lower()
    return None
