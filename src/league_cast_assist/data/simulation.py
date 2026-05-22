from __future__ import annotations

from league_cast_assist.data.asset_resolver import AssetResolver
from league_cast_assist.data.match_state import MatchStateReducer
from league_cast_assist.data.static_data import StaticDataService
from league_cast_assist.data.tooltip_formatter import TooltipFormatter
from league_cast_assist.models import ItemState, MatchState, PlayerState, TeamState

SLOTS: tuple[str, ...] = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")


async def simulated_match_state(
    static_data: StaticDataService,
    asset_resolver: AssetResolver,
    champion_ids: list[int],
    item_ids_by_player: list[list[int]] | None = None,
) -> MatchState:
    formatter = TooltipFormatter()
    reducer = MatchStateReducer(
        static_data=static_data,
        asset_resolver=asset_resolver,
        tooltip_formatter=formatter,
        item_value_sample_seconds=0,
    )
    await static_data.ensure_core_data()
    item_lookup = static_data.item_lookup()
    selected_item_ids = {
        item_id
        for item_ids in item_ids_by_player or []
        for item_id in item_ids
        if item_id in item_lookup
    }
    if selected_item_ids:
        await static_data.ensure_item_assets(selected_item_ids)

    players = []
    for index, champion_id in enumerate(champion_ids[:10]):
        champion = await static_data.champion(champion_id)
        if champion is None:
            continue
        side = "blue" if index < 5 else "red"
        selected_item_ids = []
        if item_ids_by_player and index < len(item_ids_by_player):
            selected_item_ids = item_ids_by_player[index]
        items = debug_items_for_player(
            selected_item_ids,
            item_lookup,
            asset_resolver,
            formatter,
        )
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
                items=items,
                item_value=sum((item.total_cost or 0) * max(item.count, 1) for item in items),
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


def debug_items_for_player(
    item_ids: list[int],
    item_lookup,
    asset_resolver: AssetResolver,
    formatter: TooltipFormatter,
) -> list[ItemState]:
    items = []
    for slot, item_id in enumerate(item_ids[:6]):
        if not item_id:
            continue
        item_data = item_lookup.get(item_id)
        if item_data is None:
            continue
        items.append(
            ItemState(
                item_id=item_data.item_id,
                name=item_data.name,
                icon=asset_resolver.resolve(item_data.icon_path),
                description=item_data.description,
                tooltip_html=formatter.to_rich_text(item_data.description),
                total_cost=item_data.total_cost,
                slot=slot,
                count=1,
            )
        )
    return items
