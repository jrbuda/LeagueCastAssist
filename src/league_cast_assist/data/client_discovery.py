from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import psutil

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LcuConnectionInfo:
    process_name: str
    pid: str
    port: int
    password: str
    protocol: str


class ClientDiscovery:
    """Finds local League Client connection details."""

    DEFAULT_LOCKFILE_LOCATIONS = (
        Path("C:/Riot Games/League of Legends/lockfile"),
        Path("C:/Program Files/Riot Games/League of Legends/lockfile"),
        Path("C:/Program Files (x86)/Riot Games/League of Legends/lockfile"),
    )

    def find_lockfile(self) -> Path | None:
        for path in self._candidate_lockfiles():
            if path.exists():
                return path
        return None

    def read_lcu_connection(self) -> LcuConnectionInfo | None:
        lockfile = self.find_lockfile()
        if lockfile is None:
            return None

        try:
            raw = lockfile.read_text(encoding="utf-8").strip()
            process_name, pid, port, password, protocol = raw.split(":")
            parsed_port = int(port)
        except (OSError, UnicodeDecodeError, ValueError):
            LOGGER.debug("Unable to parse LCU lockfile: %s", lockfile, exc_info=True)
            return None

        if parsed_port <= 0 or parsed_port > 65535:
            LOGGER.debug("Ignoring LCU lockfile with invalid port: %s", parsed_port)
            return None
        if protocol not in {"http", "https"}:
            LOGGER.debug("Ignoring LCU lockfile with invalid protocol: %s", protocol)
            return None
        if not password:
            LOGGER.debug("Ignoring LCU lockfile without password")
            return None

        return LcuConnectionInfo(
            process_name=process_name,
            pid=pid,
            port=parsed_port,
            password=password,
            protocol=protocol,
        )

    def _candidate_lockfiles(self) -> list[Path]:
        candidates = list(self.DEFAULT_LOCKFILE_LOCATIONS)
        candidates.extend(self._process_lockfile_candidates())

        unique_candidates: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate).lower()
            if key in seen:
                continue
            unique_candidates.append(candidate)
            seen.add(key)
        return unique_candidates

    def _process_lockfile_candidates(self) -> list[Path]:
        candidates: list[Path] = []

        for process in psutil.process_iter(["name", "cwd", "cmdline"]):
            try:
                process_name = process.info.get("name") or ""
                if "leagueclient" not in process_name.lower():
                    continue

                cwd = process.info.get("cwd")
                if cwd:
                    candidates.append(Path(cwd) / "lockfile")

                for arg in process.info.get("cmdline") or []:
                    if arg.startswith("--install-directory="):
                        install_dir = arg.split("=", 1)[1].strip('"')
                        candidates.append(Path(install_dir) / "lockfile")
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue

        return candidates
