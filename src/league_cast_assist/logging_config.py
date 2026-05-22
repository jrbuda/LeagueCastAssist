from __future__ import annotations

import logging
from pathlib import Path

from platformdirs import user_log_dir

from league_cast_assist.config import APP_AUTHOR, APP_NAME


def configure_logging() -> Path:
    log_dir = Path(user_log_dir(APP_NAME, APP_AUTHOR))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "league-cast-assist.log"

    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger(__name__).info("Logging initialized")
    return log_path
