from __future__ import annotations

from pathlib import Path

from league_cast_assist.config import cache_dir


class AssetResolver:
    COMMUNITY_DRAGON_BASE = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default"

    def __init__(self, local_assets: bool = True, version: str = "latest") -> None:
        self._local_assets = local_assets
        self._version = version
        self._asset_cache_dir = cache_dir() / "assets" / version

    def resolve(self, asset_path: str | None) -> str | None:
        if not asset_path:
            return None

        if self._local_assets:
            return str(self.local_path(asset_path))

        return self.remote_url(asset_path)

    def remote_url(self, asset_path: str) -> str:
        normalized = self._normalize_lol_game_data_path(asset_path)
        base = self.COMMUNITY_DRAGON_BASE.replace("/latest/", f"/{self._version}/")
        return f"{base}/{normalized}"

    def local_path(self, asset_path: str) -> Path:
        normalized = self._normalize_lol_game_data_path(asset_path)
        return self._asset_cache_dir / normalized

    def _normalize_lol_game_data_path(self, asset_path: str) -> str:
        normalized = asset_path.replace("\\", "/")
        prefix = "/lol-game-data/assets/"
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
        plugin_prefix = "plugins/rcp-be-lol-game-data/global/default/"
        if normalized.startswith(plugin_prefix):
            normalized = normalized[len(plugin_prefix) :]
        return normalized.lower().lstrip("/")
