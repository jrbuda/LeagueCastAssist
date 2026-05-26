"""LeagueCastAssist in-place updater.

Launched by the main app after a new exe has been downloaded.

Usage::

    updater.exe --install --source <new_exe> --target <old_exe> --pid <pid>

Behaviour:
  1. Wait up to 60 s for the main app PID to exit.
  2. Remove any leftover .bak file, then rename target -> target.bak.
  3. Move source -> target (in-place replacement).
  4. Relaunch target as a detached process.
  5. Remove .bak on success.
  6. Log every step to ``updater.log`` beside the target exe.

This module is built as a standalone ``updater.exe`` by ``build.ps1`` and
bundled inside the main ``LeagueCastAssist.exe`` via PyInstaller ``--add-data``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_LOG_PATH: Path | None = None


def _log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[updater {ts}] {message}"
    # stdout is silenced (windowed build) but keep for debug builds
    try:
        print(line, flush=True)
    except OSError:
        pass
    if _LOG_PATH is not None:
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def _wait_for_pid(pid: int, timeout: float = 60.0) -> bool:
    """Return True when *pid* is gone, False if *timeout* elapsed first."""
    try:
        import psutil  # noqa: PLC0415

        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return True
        try:
            proc.wait(timeout=timeout)
            return True
        except psutil.TimeoutExpired:
            return False
    except ImportError:
        # psutil not available – fall back to polling os.kill(pid, 0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            time.sleep(0.25)
        return False


def _install(source: Path, target: Path, pid: int) -> None:
    global _LOG_PATH  # noqa: PLW0603
    _LOG_PATH = target.parent / "updater.log"
    _log(f"Updater started – source={source} target={target} pid={pid}")

    # ── 1. Wait for main app to release its exe lock ──────────────────────
    _log(f"Waiting for PID {pid} to exit…")
    exited = _wait_for_pid(pid)
    if exited:
        _log(f"PID {pid} exited.")
    else:
        _log(f"WARNING: PID {pid} did not exit within 60 s; proceeding anyway.")
    time.sleep(0.5)  # small grace period for any lingering file handles

    # ── 2. Sanity-check the downloaded file ───────────────────────────────
    if not source.exists():
        _log(f"ERROR: Source file not found: {source}")
        sys.exit(1)

    # ── 3. Back up the running exe ────────────────────────────────────────
    backup = target.with_suffix(".exe.bak")
    if backup.exists():
        try:
            backup.unlink()
            _log(f"Removed stale backup: {backup}")
        except OSError as exc:
            _log(f"WARNING: Could not remove stale backup {backup}: {exc}")

    if target.exists():
        try:
            target.rename(backup)
            _log(f"Backed up: {target.name} -> {backup.name}")
        except OSError as exc:
            _log(f"ERROR: Could not back up {target}: {exc}")
            sys.exit(1)

    # ── 4. Move new exe into place ────────────────────────────────────────
    try:
        source.rename(target)
        _log(f"Installed: {source.name} -> {target.name}")
    except OSError as exc:
        _log(f"ERROR: Could not install new exe: {exc}")
        # Attempt rollback
        if backup.exists() and not target.exists():
            try:
                backup.rename(target)
                _log("Rollback: restored backup.")
            except OSError as rb_exc:
                _log(f"Rollback FAILED: {rb_exc}")
        sys.exit(1)

    # ── 5. Relaunch updated app ───────────────────────────────────────────
    _log(f"Launching: {target}")
    try:
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
        subprocess.Popen(  # noqa: S603
            [str(target)],
            cwd=str(target.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
        _log("Relaunch succeeded.")
    except OSError as exc:
        _log(f"ERROR: Could not relaunch {target}: {exc}")
        sys.exit(1)

    # ── 6. Clean up backup on success ─────────────────────────────────────
    try:
        if backup.exists():
            backup.unlink()
            _log(f"Removed backup: {backup.name}")
    except OSError:
        pass  # non-fatal; stale .bak is harmless

    _log("Update complete.")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LeagueCastAssist in-place updater",
        add_help=True,
    )
    parser.add_argument(
        "--install",
        action="store_true",
        required=True,
        help="Apply the in-place update",
    )
    parser.add_argument(
        "--source",
        required=True,
        metavar="PATH",
        help="Path to the downloaded new exe",
    )
    parser.add_argument(
        "--target",
        required=True,
        metavar="PATH",
        help="Path to the currently-installed exe to replace",
    )
    parser.add_argument(
        "--pid",
        type=int,
        required=True,
        metavar="PID",
        help="PID of the running main app to wait for",
    )
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    _install(
        source=Path(args.source).resolve(),
        target=Path(args.target).resolve(),
        pid=args.pid,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
