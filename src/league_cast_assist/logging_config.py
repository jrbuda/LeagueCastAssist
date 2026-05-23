from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from platformdirs import user_log_dir

from league_cast_assist.config import APP_AUTHOR, APP_NAME


def configure_logging() -> Path:
    log_dir = Path(user_log_dir(APP_NAME, APP_AUTHOR))
    log_path = log_dir / "league-cast-assist.log"

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path,
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        logging.basicConfig(
            handlers=[handler],
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            force=True,
        )
    except OSError:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            force=True,
        )
    logging.getLogger(__name__).info("Logging initialized")
    return log_path
