from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from platformdirs import user_cache_dir, user_config_dir
from pydantic import BaseModel, Field

APP_NAME = "LeagueCastAssist"
APP_AUTHOR = "LeagueCastAssist"


class AssetSettings(BaseModel):
    mode: Literal["local", "remote"] = "local"
    source: Literal["communitydragon", "datadragon"] = "communitydragon"
    version: str = "latest"


class PollingSettings(BaseModel):
    live_client_seconds: float = Field(default=2.0, gt=0)
    lcu_seconds: float = Field(default=3.0, gt=0)
    item_value_sample_seconds: float = Field(default=5.0, gt=0)


class UiSettings(BaseModel):
    theme: Literal["dark"] = "dark"


class AppSettings(BaseModel):
    assets: AssetSettings = Field(default_factory=AssetSettings)
    polling: PollingSettings = Field(default_factory=PollingSettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    first_launch_complete: bool = False
    player_name_overrides: dict[str, str] = Field(default_factory=dict)
    team_name_overrides: dict[Literal["blue", "red"], str] = Field(default_factory=dict)


def config_dir() -> Path:
    return Path(user_config_dir(APP_NAME, APP_AUTHOR))


def cache_dir() -> Path:
    return Path(user_cache_dir(APP_NAME, APP_AUTHOR))


def settings_path() -> Path:
    return config_dir() / "settings.json"


def load_settings() -> AppSettings:
    path = settings_path()
    if not path.exists():
        return AppSettings()

    try:
        return AppSettings.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return AppSettings()


def save_settings(settings: AppSettings) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    settings_path().write_text(
        json.dumps(settings.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
