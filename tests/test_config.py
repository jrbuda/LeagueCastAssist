from __future__ import annotations

import sys

from league_cast_assist.config import assets_dir, cache_dir
from league_cast_assist.data.asset_resolver import AssetResolver
from league_cast_assist.data.static_data import cdragon_version_key, is_newer_cdragon_version


def test_assets_dir_uses_working_directory_for_source_runs(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert assets_dir() == tmp_path / "assets"
    assert cache_dir() == assets_dir()


def test_asset_resolver_stores_images_directly_under_assets_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr(sys, "frozen", raising=False)

    resolver = AssetResolver(local_assets=True, version="latest")

    assert resolver.local_path("/lol-game-data/assets/v1/champion-icons/1.png") == (
        tmp_path / "assets" / "latest" / "v1" / "champion-icons" / "1.png"
    )


def test_assets_dir_uses_executable_directory_for_frozen_build(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "LeagueCastAssist.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))

    assert assets_dir() == tmp_path / "assets"
    assert cache_dir() == assets_dir()


def test_cdragon_version_comparison_detects_newer_patch() -> None:
    assert cdragon_version_key("15.10.1") == (15, 10, 1)
    assert is_newer_cdragon_version("15.10.1", "15.9.2")
    assert not is_newer_cdragon_version("15.9.2", "15.10.1")
    assert not is_newer_cdragon_version("15.10.1", None)
