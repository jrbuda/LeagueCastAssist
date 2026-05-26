from __future__ import annotations

from pathlib import Path, PurePosixPath

from league_cast_assist.config import cache_dir


class AssetResolver:
    COMMUNITY_DRAGON_BASE = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default"

    def __init__(self, local_assets: bool = True, version: str = "latest") -> None:
        self._local_assets = local_assets
        self._version = version
        self._asset_cache_dir = cache_dir() / version

    @property
    def asset_cache_dir(self) -> Path:
        return self._asset_cache_dir

    def resolve(self, asset_path: str | None) -> str | None:
        if not asset_path:
            return None

        try:
            if self._local_assets:
                # Local-only mode: always return the local path.
                # ensure_assets() is responsible for keeping every asset
                # downloaded; resolve() just points at the local file.
                return str(self.local_path(asset_path))
            else:
                # Internet mode: prefer a locally-cached copy for speed;
                # fall back to CDragon only when the file isn't on disk yet.
                local = self.local_path(asset_path)
                if local.exists():
                    return str(local)
                return self.remote_url(asset_path)
        except ValueError:
            return None

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
        normalized = normalized.lower().lstrip("/")
        parts = PurePosixPath(normalized).parts
        if (
            not normalized
            or any(part in {"", ".", ".."} for part in parts)
            or any(":" in part for part in parts)
        ):
            raise ValueError(f"Invalid asset path: {asset_path}")
        return normalized
