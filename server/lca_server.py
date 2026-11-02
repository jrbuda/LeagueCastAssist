"""LeagueCastAssist media server.

Serves the music library described by ``manifest.json`` under ``lca.budaslounge.com``:

    GET /health                        no token   service + library summary
    GET /music/manifest.json           token      what the client library should contain
    GET /media/<relative-path>         token      the bytes (Range/ETag aware)
    GET /                              no token   small service banner

Auth is a door, not a lock: the client ships the credential, so this only has to
keep casual scrapers out. Tokens are stored as sha256 hashes in ``tokens.json``
with labels and an ``enabled`` flag, so revoking or rotating never needs a
redeploy (send SIGHUP after editing, or restart the service).

Run directly (waitress if available, otherwise Flask's server):

    python3 server.py

Configuration (environment, or the systemd EnvironmentFile):

    LCA_MEDIA_ROOT          default <script dir>/lca-media
    LCA_MANIFEST            default <script dir>/manifest.json
    LCA_TOKENS_FILE         default <script dir>/tokens.json
    LCA_HOST / LCA_PORT     default 127.0.0.1 / 38030
    LCA_LOG_DIR             default <script dir>/logs
    LCA_LOG_RETENTION_DAYS  default 14
    LCA_RATE_REQUESTS       default 120   requests per minute per IP
    LCA_RATE_BURST          default 60    burst allowance per IP
    LCA_MAX_CONCURRENT      default 3     simultaneous media downloads per IP
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import signal
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file

SERVICE_NAME = "lca"
API_VERSION = 1
SCRIPT_DIR = Path(__file__).resolve().parent
STARTED_AT = datetime.now(timezone.utc).isoformat()

MEDIA_ROOT = Path(os.environ.get("LCA_MEDIA_ROOT", SCRIPT_DIR / "lca-media")).resolve()
MANIFEST_PATH = Path(os.environ.get("LCA_MANIFEST", SCRIPT_DIR / "manifest.json"))
TOKENS_FILE = Path(os.environ.get("LCA_TOKENS_FILE", SCRIPT_DIR / "tokens.json"))
LOG_DIR = Path(os.environ.get("LCA_LOG_DIR", SCRIPT_DIR / "logs"))
LOG_RETENTION_DAYS = int(os.environ.get("LCA_LOG_RETENTION_DAYS", "14"))
HOST = os.environ.get("LCA_HOST", "127.0.0.1")
PORT = int(os.environ.get("LCA_PORT", "38030"))
RATE_REQUESTS = int(os.environ.get("LCA_RATE_REQUESTS", "120"))
RATE_BURST = int(os.environ.get("LCA_RATE_BURST", "60"))
MAX_CONCURRENT = int(os.environ.get("LCA_MAX_CONCURRENT", "3"))

MEDIA_TYPES = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".jpg", ".jpeg", ".png"}

LOGGER = logging.getLogger(SERVICE_NAME)


# ── logging ────────────────────────────────────────────────────────────────
def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - LOG_RETENTION_DAYS * 86400
    removed = 0
    for old in LOG_DIR.glob("lca_*.log"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = LOG_DIR / f"lca_{stamp}.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    for handler in (logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")):
        handler.setFormatter(formatter)
        root.addHandler(handler)

    LOGGER.info(
        "logging initialized file=%s retention_days=%s removed=%s",
        log_file,
        LOG_RETENTION_DAYS,
        removed,
    )


# ── tokens ─────────────────────────────────────────────────────────────────
class TokenStore:
    """sha256-only token list with labels, so nothing secret sits on disk."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._tokens: dict[str, dict] = {}
        self.reload()

    def reload(self) -> None:
        tokens: dict[str, dict] = {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            for entry in payload.get("tokens", []):
                digest = str(entry.get("sha256", "")).strip().lower()
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    LOGGER.warning("ignoring token entry with bad hash: %s", entry.get("id"))
                    continue
                tokens[digest] = entry
        except FileNotFoundError:
            LOGGER.warning("token file missing: %s (all requests will be rejected)", self._path)
        except (OSError, ValueError):
            LOGGER.exception("token file unreadable: %s", self._path)

        with self._lock:
            self._tokens = tokens
        enabled = [entry.get("label") or entry.get("id") for entry in tokens.values()
                   if entry.get("enabled", True)]
        LOGGER.info("tokens loaded: %d total, enabled=%s", len(tokens), enabled)

    def identify(self, token: str) -> str | None:
        if not token:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            entry = self._tokens.get(digest)
        if entry is None or not entry.get("enabled", True):
            return None
        # compare_digest on the digest keeps timing uniform even though the dict
        # lookup already happened.
        if not secrets.compare_digest(digest, entry["sha256"]):
            return None
        return str(entry.get("id") or entry.get("label") or "token")


TOKENS = TokenStore(TOKENS_FILE)


# ── manifest ───────────────────────────────────────────────────────────────
class ManifestStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._raw = b"{}"
        self._data: dict = {}
        self._etag = '"empty"'
        self.reload()

    def reload(self) -> None:
        try:
            raw = self._path.read_bytes()
            data = json.loads(raw)
        except FileNotFoundError:
            LOGGER.warning("manifest missing: %s", self._path)
            raw, data = b"{}", {}
        except (OSError, ValueError):
            LOGGER.exception("manifest unreadable: %s", self._path)
            raw, data = b"{}", {}

        with self._lock:
            self._raw = raw
            self._data = data
            self._etag = '"' + hashlib.sha256(raw).hexdigest()[:32] + '"'
        tracks = data.get("tracks", []) if isinstance(data, dict) else []
        LOGGER.info(
            "manifest loaded: %d tracks, version=%s, etag=%s",
            len(tracks),
            data.get("library_version") if isinstance(data, dict) else "?",
            self._etag,
        )

    @property
    def etag(self) -> str:
        with self._lock:
            return self._etag

    def payload(self) -> dict:
        with self._lock:
            return self._data

    def raw(self) -> bytes:
        with self._lock:
            return self._raw


MANIFEST = ManifestStore(MANIFEST_PATH)


# ── rate limiting ──────────────────────────────────────────────────────────
class IpLimiter:
    """Requests/min bucket plus a concurrent-download cap, both per IP."""

    WINDOW_SECONDS = 60.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}
        self._active: dict[str, int] = {}

    def allow_request(self, ip: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(ip, deque())
            while hits and now - hits[0] > self.WINDOW_SECONDS:
                hits.popleft()
            if len(hits) >= RATE_REQUESTS + RATE_BURST:
                return False
            hits.append(now)
            return True

    def acquire_slot(self, ip: str) -> bool:
        with self._lock:
            active = self._active.get(ip, 0)
            if active >= MAX_CONCURRENT:
                return False
            self._active[ip] = active + 1
            return True

    def release_slot(self, ip: str) -> None:
        with self._lock:
            active = self._active.get(ip, 0)
            if active <= 1:
                self._active.pop(ip, None)
            else:
                self._active[ip] = active - 1


LIMITER = IpLimiter()


# ── app ────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # we never accept uploads


def client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def presented_token() -> str:
    token = request.headers.get("X-LCA-Token", "").strip()
    if token:
        return token
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def unauthorized(detail: str = "Missing or invalid token.") -> Response:
    return jsonify({"error": "unauthorized", "detail": detail}), 401


def guard(require_token: bool) -> Response | None:
    ip = client_ip()
    if not LIMITER.allow_request(ip):
        return jsonify({"error": "rate_limited", "detail": "Too many requests."}), 403
    if require_token:
        token_id = TOKENS.identify(presented_token())
        if token_id is None:
            LOGGER.info("denied ip=%s path=%s", ip, request.path)
            return unauthorized("Missing or invalid token. Update LeagueCastAssist.")
        request.environ["lca.token_id"] = token_id
    return None


def library_summary() -> dict:
    payload = MANIFEST.payload()
    tracks = payload.get("tracks", []) if isinstance(payload, dict) else []
    return {
        "library_version": payload.get("library_version", ""),
        "tracks": len(tracks),
        "bytes": sum(int(track.get("size_bytes") or 0) for track in tracks),
        "artwork": len({track.get("artwork") for track in tracks if track.get("artwork")}),
        "manifest_etag": MANIFEST.etag,
    }


@app.get("/")
def root() -> Response:
    return jsonify(
        {
            "service": SERVICE_NAME,
            "api": API_VERSION,
            "endpoints": ["/health", "/music/manifest.json", "/media/<path>"],
        }
    )


@app.get("/health")
def health() -> Response:
    return jsonify(
        {
            "ok": True,
            "service": SERVICE_NAME,
            "api": API_VERSION,
            "started_at": STARTED_AT,
            "music": library_summary(),
        }
    )


@app.get("/music/manifest.json")
def manifest() -> Response:
    denied = guard(require_token=True)
    if denied is not None:
        return denied
    if request.headers.get("If-None-Match") == MANIFEST.etag:
        return Response(status=304, headers={"ETag": MANIFEST.etag})
    return Response(
        MANIFEST.raw(),
        mimetype="application/json",
        headers={
            "ETag": MANIFEST.etag,
            "Cache-Control": "no-cache",
            "X-LCA-Token-Id": request.environ.get("lca.token_id", ""),
        },
    )


def safe_media_path(relative: str) -> Path | None:
    candidate = (MEDIA_ROOT / relative).resolve()
    try:
        candidate.relative_to(MEDIA_ROOT)
    except ValueError:
        return None
    if candidate.suffix.lower() not in MEDIA_TYPES or not candidate.is_file():
        return None
    return candidate


@app.get("/media/<path:relative>")
def media(relative: str) -> Response:
    denied = guard(require_token=True)
    if denied is not None:
        return denied

    ip = client_ip()
    if not LIMITER.acquire_slot(ip):
        return jsonify({"error": "busy", "detail": "Too many simultaneous downloads."}), 403

    started = time.monotonic()
    try:
        target = safe_media_path(relative)
        if target is None:
            LOGGER.info("not found ip=%s path=%s", ip, relative)
            return jsonify({"error": "not_found", "path": relative}), 404

        mimetype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        response = send_file(
            target,
            mimetype=mimetype,
            conditional=True,
            etag=True,
            last_modified=target.stat().st_mtime,
            max_age=31536000,
        )
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        LOGGER.info(
            "media ip=%s token=%s path=%s bytes=%s ms=%d",
            ip,
            request.environ.get("lca.token_id", ""),
            relative,
            target.stat().st_size,
            int((time.monotonic() - started) * 1000),
        )
        return response
    finally:
        LIMITER.release_slot(ip)


@app.errorhandler(404)
def not_found(_error) -> Response:
    return jsonify({"error": "not_found", "path": request.path}), 404


def reload_runtime(_signum=None, _frame=None) -> None:  # noqa: ANN001
    LOGGER.info("SIGHUP: reloading manifest and tokens")
    MANIFEST.reload()
    TOKENS.reload()


def main() -> int:
    configure_logging()
    if not MANIFEST.payload():
        LOGGER.warning("starting with an empty manifest - run make_manifest.py")
    signal.signal(signal.SIGHUP, reload_runtime)

    LOGGER.info(
        "starting %s api=%d media_root=%s host=%s port=%s",
        SERVICE_NAME,
        API_VERSION,
        MEDIA_ROOT,
        HOST,
        PORT,
    )
    try:
        from waitress import serve  # noqa: PLC0415
    except ImportError:
        LOGGER.warning("waitress not installed; falling back to the Flask dev server")
        app.run(host=HOST, port=PORT, threaded=True)
        return 0

    serve(app, host=HOST, port=PORT, threads=8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

