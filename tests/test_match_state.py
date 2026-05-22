from __future__ import annotations

import pytest

from league_cast_assist.data.asset_resolver import AssetResolver
from league_cast_assist.data.match_state import MatchStateReducer, objective_events_from_events
from league_cast_assist.data.simulation import simulated_match_state
from league_cast_assist.data.static_data import ChampionData, ItemData
from league_cast_assist.data.tooltip_formatter import TooltipFormatter
from league_cast_assist.tools.validate_static_data import rendered_brace_issue


class FakeStaticData:
    def __init__(self) -> None:
        self.core_loaded = False

    async def ensure_core_data(self) -> None:
        self.core_loaded = True

    async def ensure_item_assets(self, item_ids: set[int]) -> None:
        self.item_asset_ids = item_ids

    def item_lookup(self) -> dict[int, ItemData]:
        return {
            1055: ItemData(
                item_id=1055,
                name="Doran's Blade",
                icon_path="/lol-game-data/assets/items/1055.png",
                description="<mainText>Starter item</mainText>",
                total_cost=450,
            )
        }

    def summoners_rift_item_ids(self) -> list[int]:
        return [1055]

    def champion_summary(self) -> dict[int, object]:
        return {}

    def champion_id_for_name(self, name: str | None) -> int | None:
        return 1 if name == "Annie" else None

    async def champion(self, champion_id: int) -> ChampionData | None:
        if champion_id != 1:
            return None
        return ChampionData(
            champion_id=1,
            name="Annie",
            icon_path="/lol-game-data/assets/v1/champion-icons/1.png",
            passive={
                "name": "Pyromania",
                "abilityIconPath": "/lol-game-data/assets/annie_p.png",
                "description": "Stuns after spell casts.",
            },
            spells=[
                {
                    "spellKey": "q",
                    "name": "Disintegrate",
                    "abilityIconPath": "/lol-game-data/assets/annie_q.png",
                    "description": "Annie hurls a fireball.",
                    "cooldown": "4s",
                    "cost": "60 Mana",
                    "range": [625, 625, 625],
                }
            ],
        )


@pytest.mark.anyio
async def test_reducer_builds_live_state_with_item_value() -> None:
    reducer = MatchStateReducer(
        static_data=FakeStaticData(),
        asset_resolver=AssetResolver(local_assets=False),
        tooltip_formatter=TooltipFormatter(),
        item_value_sample_seconds=0,
    )

    state = await reducer.apply_live_client_data(
        {
            "gameData": {"gameTime": 123.4},
            "allPlayers": [
                {
                    "riotId": "Blue Player#NA1",
                    "riotIdGameName": "Blue Player",
                    "riotIdTagLine": "NA1",
                    "team": "ORDER",
                    "championName": "Annie",
                    "position": "TOP",
                    "level": 6,
                    "scores": {"kills": 1, "deaths": 0, "assists": 2, "creepScore": 50},
                    "items": [{"itemID": 1055, "slot": 0, "price": 400}],
                },
                {
                    "summonerName": "Red Player",
                    "team": "CHAOS",
                    "championName": "Annie",
                    "items": [],
                },
            ],
        }
    )

    assert state.phase == "InProgress"
    assert state.blue_team.players[0].display_name == "Blue Player"
    assert state.blue_team.players[0].riot_id == "Blue Player#NA1"
    assert state.blue_team.players[0].position == "TOP"
    assert state.blue_team.players[0].champion_name == "Annie"
    assert state.blue_team.players[0].abilities[0].name == "Pyromania"
    assert state.blue_team.players[0].abilities[1].name == "Disintegrate"
    assert state.blue_team.players[0].item_value == 450
    assert state.red_team.players[0].display_name == "Red Player"
    assert state.item_value_samples[-1].blue_total == 450
    assert state.item_value_samples[-1].red_total == 0


def test_objective_events_are_mapped_for_timeline() -> None:
    events = objective_events_from_events(
        [
            {
                "EventName": "DragonKill",
                "EventTime": 365.0,
                "DragonType": "Infernal",
                "KillerName": "Blue Player",
            },
            {
                "EventName": "TurretKilled",
                "EventTime": 500.0,
                "TurretKilled": "Turret_T2_C_03_A",
            },
        ],
        {"blue player": "blue"},
    )

    assert events[0].team_side == "blue"
    assert events[0].objective_type == "infernal_dragon"
    assert events[0].label == "Infernal Dragon"
    assert events[1].team_side == "blue"
    assert events[1].objective_type == "tower"


def test_static_validator_flags_visible_brace_output() -> None:
    assert rendered_brace_issue(
        "Aurelion Sol R Falling Star",
        "stat_lines",
        "{1d7ce9ef}: 42",
    ) == "Aurelion Sol R Falling Star: visible brace output in stat_lines: {1d7ce9ef}: 42"
    assert rendered_brace_issue("Annie Q Disintegrate", "tooltip", "Deals 80 damage.") is None


@pytest.mark.anyio
async def test_debug_simulation_builds_team_state() -> None:
    state = await simulated_match_state(
        static_data=FakeStaticData(),
        asset_resolver=AssetResolver(local_assets=False),
        champion_ids=[1] * 10,
        item_ids_by_player=[[1055], [], [], [], [], [], [], [], [], []],
    )

    assert state.source == "debug"
    assert len(state.blue_team.players) == 5
    assert len(state.red_team.players) == 5
    assert state.blue_team.players[0].champion_name == "Annie"
    assert state.blue_team.players[0].abilities[0].name == "Pyromania"
    assert state.blue_team.players[0].items[0].name == "Doran's Blade"
    assert state.blue_team.players[0].item_value == 450
