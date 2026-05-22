from __future__ import annotations

from league_cast_assist.data.asset_resolver import AssetResolver
from league_cast_assist.data.match_state import MatchStateReducer
from league_cast_assist.data.static_data import StaticDataService
from league_cast_assist.data.tooltip_formatter import TooltipFormatter
from league_cast_assist.models import MatchState, PlayerState, TeamState

SLOTS: tuple[str, ...] = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")


async def simulated_match_state(
    static_data: StaticDataService,
    asset_resolver: AssetResolver,
    champion_ids: list[int],
) -> MatchState:
    reducer = MatchStateReducer(
        static_data=static_data,
        asset_resolver=asset_resolver,
        tooltip_formatter=TooltipFormatter(),
        item_value_sample_seconds=0,
    )
    await static_data.ensure_core_data()

    players = []
    for index, champion_id in enumerate(champion_ids[:10]):
        champion = await static_data.champion(champion_id)
        if champion is None:
            continue
        side = "blue" if index < 5 else "red"
        players.append(
            PlayerState(
                stable_key=f"debug:{index}:{champion_id}",
                display_name=f"Debug {index + 1}",
                riot_id=f"Debug {index + 1}#SIM",
                team_side=side,
                position=debug_position(index),
                champion_id=champion.champion_id,
                champion_name=champion.name,
                champion_icon=asset_resolver.resolve(champion.icon_path),
                abilities=reducer._abilities_from_champion(champion),
                level=18,
                kills=0,
                deaths=0,
                assists=0,
                creep_score=0,
            )
        )

    state = MatchState(
        phase="DebugSimulation",
        status="Debug simulation active",
        source="debug",
        blue_team=TeamState(
            side="blue",
            display_name="Blue Debug",
            players=[player for player in players if player.team_side == "blue"],
        ),
        red_team=TeamState(
            side="red",
            display_name="Red Debug",
            players=[player for player in players if player.team_side == "red"],
        ),
    )
    return state


def debug_position(index: int) -> str:
    return SLOTS[index % 5]
