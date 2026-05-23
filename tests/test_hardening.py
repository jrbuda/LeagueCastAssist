from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from league_cast_assist.config import AppSettings
from league_cast_assist.data.asset_resolver import AssetResolver
from league_cast_assist.data.client_discovery import ClientDiscovery
from league_cast_assist.data.controller import AppController
from league_cast_assist.data.match_state import MatchStateReducer, objective_counts_from_events
from league_cast_assist.data.static_data import StaticDataService, asset_file_looks_valid
from league_cast_assist.data.tooltip_formatter import TooltipFormatter
from league_cast_assist.models import MatchState, PlayerState


def test_lockfile_parse_failure_returns_none(monkeypatch, tmp_path) -> None:
    lockfile = tmp_path / "lockfile"
    lockfile.write_text("LeagueClient:bad", encoding="utf-8")

    discovery = ClientDiscovery()
    monkeypatch.setattr(discovery, "find_lockfile", lambda: lockfile)

    assert discovery.read_lcu_connection() is None


def test_lockfile_invalid_port_returns_none(monkeypatch, tmp_path) -> None:
    lockfile = tmp_path / "lockfile"
    lockfile.write_text("LeagueClient:123:not-a-port:secret:https", encoding="utf-8")

    discovery = ClientDiscovery()
    monkeypatch.setattr(discovery, "find_lockfile", lambda: lockfile)

    assert discovery.read_lcu_connection() is None


def test_asset_resolver_rejects_path_traversal(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    resolver = AssetResolver(local_assets=True, version="latest")

    with pytest.raises(ValueError):
        resolver.local_path("/lol-game-data/assets/../../outside.png")

    assert resolver.resolve("/lol-game-data/assets/../../outside.png") is None


def test_corrupt_cached_json_is_ignored_and_removed(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    cache_file = tmp_path / "assets" / "communitydragon" / "latest" / "items.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("{not-json", encoding="utf-8")

    service = StaticDataService(version="latest", download_assets=False)

    assert service._read_json("items.json") is None
    assert not cache_file.exists()


def test_asset_file_validation_rejects_empty_file(tmp_path) -> None:
    asset = tmp_path / "1.png"
    asset.write_bytes(b"")

    assert not asset_file_looks_valid(asset)


def test_tooltip_formatter_balances_unmatched_closing_span() -> None:
    result = TooltipFormatter().to_rich_text("Damage</magicDamage> then <magicDamage>more")

    assert result.count("<span") == result.count("</span>")


def test_objective_counts_exclude_turret_plates() -> None:
    counts = objective_counts_from_events(
        [
            {"EventName": "TurretPlateDestroyed", "KillerTeam": "ORDER"},
            {"EventName": "TurretKilled", "KillerTeam": "ORDER"},
        ],
        {},
    )

    assert counts["blue"]["towers"] == 1


def test_inhibitor_killed_side_fallback_uses_inhibitor_field() -> None:
    events = [
        {
            "EventName": "InhibitorKilled",
            "EventTime": 1200.0,
            "InhibitorKilled": "Barracks_T1_C1",
        }
    ]

    counts = objective_counts_from_events(events, {})

    assert counts["red"]["inhibitors"] == 1


@pytest.mark.anyio
async def test_controller_marks_stale_live_data_without_clearing_state(monkeypatch) -> None:
    states: list[MatchState] = []
    statuses: list[str] = []
    controller = AppController(
        AppSettings(),
        state_callback=states.append,
        status_callback=statuses.append,
    )
    controller._has_live_state = True
    controller._reducer._state.status = "Live game data connected"
    controller._reducer._state.source = "liveclient"
    controller._reducer._state.blue_team.display_name = "Keep Blue"
    monkeypatch.setattr(controller, "_should_poll_lcu", lambda: False)

    async def fail_live_data():
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(controller._live_client, "all_game_data", fail_live_data)

    await controller.poll_once()

    assert states[-1].blue_team.display_name == "Keep Blue"
    assert "connection lost" in states[-1].status.lower()
    assert statuses[-1] == states[-1].status


def test_champion_abilities_are_cached(monkeypatch) -> None:
    from league_cast_assist.data.static_data import ChampionData

    champion = ChampionData(
        champion_id=1,
        name="Annie",
        icon_path=None,
        passive={"name": "Passive", "description": "Passive text"},
        spells=[{"spellKey": "q", "name": "Q", "description": "Q text"}],
        bin_data={
            "Characters/Annie/Spells/AnnieQAbility/AnnieQ": {
                "ObjectName": "AnnieQ",
                "mSpell": {"DataValues": [{"name": "Damage", "values": [0, 1, 2]}]},
            }
        },
        alias="Annie",
    )
    reducer = MatchStateReducer(None, AssetResolver(local_assets=False), TooltipFormatter())
    calls = {"count": 0}
    original = reducer._spell_bin_for_slot

    def counted(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(reducer, "_spell_bin_for_slot", counted)

    reducer._abilities_from_champion(champion)
    reducer._abilities_from_champion(champion)

    assert calls["count"] == 5


def test_static_data_atomic_write_replaces_complete_file(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    service = StaticDataService(version="latest", download_assets=False)
    target = tmp_path / "assets" / "communitydragon" / "latest" / "items.json"

    service._write_bytes_atomic(target, json.dumps([{"id": 1}]).encode("utf-8"))

    assert service._read_json("items.json") == [{"id": 1}]
    assert not Path(f"{target}.tmp").exists()


# ---------------------------------------------------------------------------
# New regression tests for graph / controller hardening
# ---------------------------------------------------------------------------


def test_role_label_prefers_champion_name_over_position() -> None:
    """role_label() must show champion name in kill graph inline labels."""
    from league_cast_assist.ui.graph import role_label

    player = PlayerState(
        stable_key="summoner#tag",
        display_name="summoner#tag",
        position="TOP",
        champion_name="Garen",
        team_side="blue",
    )
    assert role_label(player) == "Garen"


def test_role_label_falls_back_to_position_when_no_champion() -> None:
    from league_cast_assist.ui.graph import role_label

    player = PlayerState(
        stable_key="summoner#tag",
        display_name="summoner#tag",
        position="JUNGLE",
        champion_name=None,
        team_side="blue",
    )
    assert role_label(player) == "Jungle"


@pytest.mark.anyio
async def test_poll_live_client_exception_does_not_kill_run_loop(monkeypatch) -> None:
    """apply_live_client_data raising must not propagate out of poll_once."""
    states: list[MatchState] = []
    controller = AppController(
        AppSettings(),
        state_callback=states.append,
        status_callback=lambda _: None,
    )
    monkeypatch.setattr(controller, "_should_poll_lcu", lambda: False)

    good_payload: dict = {"gameData": {"gameTime": 1.0}, "allPlayers": [], "events": {"Events": []}}

    async def good_live_data():
        return good_payload

    async def bad_apply(payload):  # noqa: ARG001
        raise KeyError("unexpected_field")

    monkeypatch.setattr(controller._live_client, "all_game_data", good_live_data)
    monkeypatch.setattr(controller._reducer, "apply_live_client_data", bad_apply)

    # Must not raise; the exception must be swallowed and logged.
    result = await controller.poll_once()

    assert result.live_connected is True  # payload was received
    assert len(states) == 1  # state_callback was still called


@pytest.mark.anyio
async def test_run_loop_continues_after_poll_once_raises(monkeypatch) -> None:
    """A stray exception from poll_once must not terminate the run loop."""
    import asyncio

    call_count = 0
    controller = AppController(
        AppSettings(),
        state_callback=lambda _: None,
        status_callback=lambda _: None,
    )

    original_poll = controller.poll_once

    async def patched_poll():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated crash on first poll")
        controller.stop()
        return await original_poll()

    monkeypatch.setattr(controller, "poll_once", patched_poll)
    monkeypatch.setattr(controller, "_initialize_static_data", lambda: asyncio.sleep(0))

    # run() must return cleanly; loop must have called poll_once twice.
    await controller.run()

    assert call_count == 2


@pytest.mark.anyio
async def test_poll_lcu_catches_oserror(monkeypatch) -> None:
    """_poll_lcu must handle OSError from the LCU HTTP client without raising."""
    from league_cast_assist.data.controller import PollResult

    controller = AppController(
        AppSettings(),
        state_callback=lambda _: None,
        status_callback=lambda _: None,
    )

    class FakeLcuClient:
        async def gameflow_phase(self):
            raise OSError("disk read failure")

    monkeypatch.setattr(controller, "_get_lcu_client", lambda: FakeLcuClient())

    result = PollResult()
    # Must not raise; OSError must be caught inside _poll_lcu.
    await controller._poll_lcu(result)
    assert result.lcu_connected is False
