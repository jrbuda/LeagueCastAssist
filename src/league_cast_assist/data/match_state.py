from __future__ import annotations

import re
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from league_cast_assist.data.ability_math import (
    SpellBinData,
    resolve_tooltip_placeholders,
    unresolved_placeholders,
)
from league_cast_assist.data.asset_resolver import AssetResolver
from league_cast_assist.data.static_data import ChampionData, ItemData, StaticDataService
from league_cast_assist.data.tooltip_formatter import TooltipFormatter
from league_cast_assist.models import (
    AbilitySlot,
    AbilityState,
    ItemState,
    ItemValueSample,
    MatchState,
    ObjectiveEvent,
    PlayerState,
    TeamSide,
    TeamState,
)


@dataclass
class PreGamePlayer:
    display_name: str | None = None
    champion_id: int | None = None
    assigned_position: str | None = None


@dataclass
class PreGameState:
    blue: dict[str, PreGamePlayer] = field(default_factory=dict)
    red: dict[str, PreGamePlayer] = field(default_factory=dict)
    blue_team_name: str = "Blue Team"
    red_team_name: str = "Red Team"


class MatchStateReducer:
    """Builds stable UI state from raw League and static-data payloads."""

    def __init__(
        self,
        static_data: StaticDataService,
        asset_resolver: AssetResolver,
        tooltip_formatter: TooltipFormatter,
        item_value_sample_seconds: float = 5.0,
    ) -> None:
        self._static_data = static_data
        self._asset_resolver = asset_resolver
        self._tooltip_formatter = tooltip_formatter
        self._item_value_sample_seconds = item_value_sample_seconds
        self._state = MatchState()
        self._pregame = PreGameState()
        self._last_sample_monotonic = 0.0
        self._last_events: list[dict[str, Any]] = []

    @property
    def state(self) -> MatchState:
        return self._state

    def apply_lcu_phase(self, phase: str | None) -> MatchState:
        if phase:
            self._state.phase = phase
            self._state.source = "lcu"
            self._state.status = f"League client connected: {phase}"
        return self._state

    def apply_lobby(self, payload: dict | None) -> MatchState:
        if not payload:
            return self._state

        raw_game_config = payload.get("gameConfig")
        game_config = raw_game_config if isinstance(raw_game_config, dict) else {}
        custom_team100 = game_config.get("teamOneName")
        custom_team200 = game_config.get("teamTwoName")
        if isinstance(custom_team100, str) and custom_team100.strip():
            self._pregame.blue_team_name = custom_team100.strip()
        if isinstance(custom_team200, str) and custom_team200.strip():
            self._pregame.red_team_name = custom_team200.strip()

        members = payload.get("members")
        if isinstance(members, list):
            for index, member in enumerate(members):
                if not isinstance(member, dict):
                    continue
                side = team_side_from_any(member.get("teamId")) or ("blue" if index < 5 else "red")
                key = stable_key_from_payload(member, fallback=f"lobby-{side}-{index}")
                player = self._pregame_team(side).setdefault(key, PreGamePlayer())
                player.display_name = display_name_from_payload(member) or player.display_name

        self._sync_pregame_to_state()
        return self._state

    async def apply_champ_select(self, payload: dict | None) -> MatchState:
        if not payload:
            return self._state

        for team_key, side in (("myTeam", "blue"), ("theirTeam", "red")):
            team = payload.get(team_key)
            if not isinstance(team, list):
                continue
            for index, raw_player in enumerate(team):
                if not isinstance(raw_player, dict):
                    continue
                key = stable_key_from_payload(raw_player, fallback=f"champ-select-{side}-{index}")
                player = self._pregame_team(side).setdefault(key, PreGamePlayer())
                player.display_name = display_name_from_payload(raw_player) or player.display_name
                champion_id = int_or_none(raw_player.get("championId"))
                if champion_id and champion_id > 0:
                    player.champion_id = champion_id
                position = raw_player.get("assignedPosition")
                if isinstance(position, str) and position:
                    player.assigned_position = position

        await self._sync_pregame_to_state_async()
        return self._state

    async def apply_live_client_data(self, payload: dict) -> MatchState:
        game_data = payload.get("gameData") if isinstance(payload.get("gameData"), dict) else {}
        game_time = float_or_none(game_data.get("gameTime"))
        if game_time is not None:
            self._state.game_time_seconds = game_time
            self._state.phase = "InProgress"
            self._state.source = "liveclient"
            self._state.status = "Live game data connected"

        players = payload.get("allPlayers")
        if not isinstance(players, list):
            return self._state

        raw_events = payload.get("events") if isinstance(payload.get("events"), dict) else {}
        event_list = raw_events.get("Events") if isinstance(raw_events, dict) else None
        self._last_events = (
            [event for event in event_list if isinstance(event, dict)]
            if isinstance(event_list, list)
            else []
        )

        blue_players: list[PlayerState] = []
        red_players: list[PlayerState] = []
        live_item_ids: set[int] = set()

        for index, raw_player in enumerate(players):
            if not isinstance(raw_player, dict):
                continue

            side = team_side_from_any(raw_player.get("team")) or ("blue" if index < 5 else "red")
            player = await self._player_from_live_payload(raw_player, side, index)
            live_item_ids.update(item.item_id for item in player.items)
            if side == "blue":
                blue_players.append(player)
            else:
                red_players.append(player)

        if live_item_ids:
            await self._static_data.ensure_item_assets(live_item_ids)

        blue_players = sorted(blue_players, key=player_sort_key)
        red_players = sorted(red_players, key=player_sort_key)

        self._state.blue_team = TeamState(
            side="blue",
            display_name=self._pregame.blue_team_name,
            players=blue_players,
        )
        self._state.red_team = TeamState(
            side="red",
            display_name=self._pregame.red_team_name,
            players=red_players,
        )
        self._state.objective_events = objective_events_from_events(
            self._last_events,
            self._player_side_lookup_from_players([*blue_players, *red_players]),
        )
        self._maybe_add_item_value_sample()
        return self._state

    async def _player_from_live_payload(
        self,
        raw_player: dict[str, Any],
        side: TeamSide,
        index: int,
    ) -> PlayerState:
        display_name = display_name_from_payload(raw_player) or f"{side.title()} Player {index + 1}"
        champion_id = int_or_none(raw_player.get("championId"))
        champion_name = string_or_none(raw_player.get("championName"))
        if champion_id is None:
            champion_id = self._static_data.champion_id_for_name(champion_name)

        champion = await self._static_data.champion(champion_id) if champion_id else None
        abilities = self._abilities_from_champion(champion)
        items = self._items_from_live_payload(raw_player)
        scores = raw_player.get("scores") if isinstance(raw_player.get("scores"), dict) else {}

        return PlayerState(
            stable_key=stable_key_from_payload(
                raw_player,
                fallback=f"live-{side}-{index}-{display_name}",
            ),
            display_name=display_name,
            riot_id=riot_id_from_payload(raw_player),
            team_side=side,
            position=string_or_none(raw_player.get("position")),
            champion_id=champion_id,
            champion_name=champion.name if champion else champion_name,
            champion_icon=self._asset_resolver.resolve(champion.icon_path if champion else None),
            abilities=abilities,
            items=items,
            item_value=sum((item.total_cost or 0) * max(item.count, 1) for item in items),
            level=int_or_none(raw_player.get("level")),
            kills=int_or_none(scores.get("kills")),
            deaths=int_or_none(scores.get("deaths")),
            assists=int_or_none(scores.get("assists")),
            creep_score=int_or_none(scores.get("creepScore")),
            ward_score=float_or_none(scores.get("wardScore")),
        )

    def _abilities_from_champion(self, champion: ChampionData | None) -> list[AbilityState]:
        if champion is None:
            return []

        abilities: list[AbilityState] = []
        raw_by_slot: dict[AbilitySlot, dict[str, Any]] = {}
        if champion.passive:
            passive = dict(champion.passive)
            if champion.passive_tooltip:
                passive["dynamicDescription"] = champion.passive_tooltip
            raw_by_slot["P"] = passive

        seen_slots: set[str] = set()
        for spell in champion.spells:
            slot = str(spell.get("spellKey") or "").upper()
            if slot in {"Q", "W", "E", "R"} and slot not in seen_slots:
                spell_data = dict(spell)
                if champion.spell_tooltips and champion.spell_tooltips.get(slot):
                    spell_data["dynamicDescription"] = champion.spell_tooltips[slot]
                raw_by_slot[slot] = spell_data
                seen_slots.add(slot)

        spell_bins: dict[AbilitySlot, SpellBinData] = {
            slot: self._spell_bin_for_slot(champion, slot, raw_by_slot.get(slot))
            for slot in ("P", "Q", "W", "E", "R")
        }
        linked_bins = self._linked_spell_bins(champion, spell_bins)
        if "P" in raw_by_slot:
            abilities.append(
                self._ability_from_raw(
                    "P",
                    raw_by_slot["P"],
                    spell_bins["P"],
                    linked_bins,
                )
            )

        for slot in ("Q", "W", "E", "R"):
            spell_data = raw_by_slot.get(slot)
            if spell_data is not None:
                abilities.append(
                    self._ability_from_raw(slot, spell_data, spell_bins[slot], linked_bins)
                )

        order = {"P": 0, "Q": 1, "W": 2, "E": 3, "R": 4}
        return sorted(abilities, key=lambda ability: order[ability.slot])

    def _ability_from_raw(
        self,
        slot: AbilitySlot,
        raw_ability: dict[str, Any],
        spell_bin: SpellBinData,
        linked_bins: dict[str, SpellBinData] | None = None,
    ) -> AbilityState:
        raw_description = string_or_none(raw_ability.get("dynamicDescription")) or string_or_none(
            raw_ability.get("description")
        )
        description = resolve_tooltip_placeholders(
            raw_description,
            spell_bin,
            linked_bins,
        )
        stat_lines = spell_bin.stat_lines()
        if slot == "P" and not [line for line in stat_lines if line != "Cooldown: 0s"]:
            stat_lines.extend(spell_bin.data_value_lines())
        if slot == "P":
            description = expanded_passive_description(description, stat_lines)
        cooldown = format_series_with_suffix(spell_bin.cooldown, "s", require_positive=True)
        cost = format_series_with_suffix(spell_bin.cost, "", require_positive=True)
        ability_range = format_series_with_suffix(spell_bin.range, "", require_positive=True)
        if ability_range is None:
            ability_range = format_number_list(raw_ability.get("range"), require_positive=True)
        return AbilityState(
            slot=slot,
            name=string_or_none(raw_ability.get("name")) or "Unknown Ability",
            icon=self._asset_resolver.resolve(string_or_none(raw_ability.get("abilityIconPath"))),
            short_description=string_or_none(raw_ability.get("description")),
            full_description=description,
            tooltip_html=self._tooltip_formatter.to_rich_text(description),
            stat_lines=stat_lines,
            cooldown=cooldown,
            cost=cost,
            range=ability_range,
        )

    def _spell_bin_for_slot(
        self,
        champion: ChampionData,
        slot: AbilitySlot,
        raw_ability: dict[str, Any] | None = None,
    ) -> SpellBinData:
        if not champion.bin_data:
            return SpellBinData()

        candidates = []
        for key, value in champion.bin_data.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            spell = value.get("mSpell")
            if isinstance(spell, dict) and (
                spell.get("DataValues")
                or spell.get("mSpellCalculations")
                or spell.get("cooldownTime")
            ):
                candidates.append((key, value))

        if not candidates:
            return SpellBinData()

        if slot == "P":
            candidates = [
                candidate
                for candidate in candidates
                if passive_candidate_has_evidence(candidate, champion, raw_ability)
            ]
            if not candidates:
                return SpellBinData()

        selected = max(
            candidates,
            key=lambda candidate: spell_candidate_score(candidate, champion, slot, raw_ability),
        )
        return SpellBinData.from_raw(selected[1])

    def _linked_spell_bins(
        self,
        champion: ChampionData,
        spell_bins: dict[AbilitySlot, SpellBinData],
    ) -> dict[str, SpellBinData]:
        alias = "".join(character.lower() for character in champion.alias if character.isalnum())
        if not alias:
            return {}
        return {
            f"{alias}passive": spell_bins["P"],
            f"{alias}p": spell_bins["P"],
            f"{alias}q": spell_bins["Q"],
            f"{alias}w": spell_bins["W"],
            f"{alias}e": spell_bins["E"],
            f"{alias}r": spell_bins["R"],
        }

    def _items_from_live_payload(self, raw_player: dict[str, Any]) -> list[ItemState]:
        raw_items = raw_player.get("items")
        if not isinstance(raw_items, list):
            return []

        item_lookup = self._static_data.item_lookup()
        items: list[ItemState] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            item_id = int_or_none(
                raw_item.get("itemID") or raw_item.get("itemId") or raw_item.get("id")
            )
            if not item_id:
                continue

            item_data = item_lookup.get(item_id)
            items.append(self._item_from_data(item_id, item_data, raw_item))
        return items

    def _item_from_data(
        self,
        item_id: int,
        item_data: ItemData | None,
        raw_item: dict[str, Any],
    ) -> ItemState:
        raw_name = string_or_none(raw_item.get("displayName")) or string_or_none(
            raw_item.get("name")
        )
        description = item_data.description if item_data else string_or_none(
            raw_item.get("description")
        )
        raw_total_cost = int_or_none(raw_item.get("priceTotal") or raw_item.get("price"))
        total_cost = (
            item_data.total_cost
            if item_data and item_data.total_cost is not None
            else raw_total_cost
        )
        return ItemState(
            item_id=item_id,
            name=item_data.name if item_data else raw_name or f"Item {item_id}",
            icon=self._asset_resolver.resolve(item_data.icon_path if item_data else None),
            description=description,
            tooltip_html=self._tooltip_formatter.to_rich_text(description),
            total_cost=total_cost,
            slot=int_or_none(raw_item.get("slot")),
            count=int_or_none(raw_item.get("count")) or 1,
        )

    async def _sync_pregame_to_state_async(self) -> None:
        await self._set_pregame_team("blue")
        await self._set_pregame_team("red")

    def _sync_pregame_to_state(self) -> None:
        self._state.blue_team = TeamState(
            side="blue",
            display_name=self._pregame.blue_team_name,
            players=[
                self._pregame_player_to_state("blue", key, value)
                for key, value in self._pregame.blue.items()
            ],
        )
        self._state.red_team = TeamState(
            side="red",
            display_name=self._pregame.red_team_name,
            players=[
                self._pregame_player_to_state("red", key, value)
                for key, value in self._pregame.red.items()
            ],
        )

    async def _set_pregame_team(self, side: TeamSide) -> None:
        players: list[PlayerState] = []
        for key, pregame_player in self._pregame_team(side).items():
            champion = (
                await self._static_data.champion(pregame_player.champion_id)
                if pregame_player.champion_id
                else None
            )
            players.append(
                PlayerState(
                    stable_key=key,
                    display_name=pregame_player.display_name or "Unknown Player",
                    team_side=side,
                    champion_id=pregame_player.champion_id,
                    champion_name=champion.name if champion else None,
                    champion_icon=self._asset_resolver.resolve(
                        champion.icon_path if champion else None
                    ),
                    abilities=self._abilities_from_champion(champion),
                )
            )

        if side == "blue":
            self._state.blue_team = TeamState(
                side="blue",
                display_name=self._pregame.blue_team_name,
                players=players,
            )
        else:
            self._state.red_team = TeamState(
                side="red",
                display_name=self._pregame.red_team_name,
                players=players,
            )

    def _pregame_player_to_state(
        self,
        side: TeamSide,
        key: str,
        pregame_player: PreGamePlayer,
    ) -> PlayerState:
        return PlayerState(
            stable_key=key,
            display_name=pregame_player.display_name or "Unknown Player",
            team_side=side,
            champion_id=pregame_player.champion_id,
        )

    def _pregame_team(self, side: TeamSide) -> dict[str, PreGamePlayer]:
        return self._pregame.blue if side == "blue" else self._pregame.red

    def _maybe_add_item_value_sample(self) -> None:
        game_time = self._state.game_time_seconds
        if game_time is None:
            return

        now = monotonic()
        if now - self._last_sample_monotonic < self._item_value_sample_seconds:
            return

        blue_total = sum(player.item_value for player in self._state.blue_team.players)
        red_total = sum(player.item_value for player in self._state.red_team.players)
        objective_counts = objective_counts_from_events(
            self._last_events,
            self._player_side_lookup(),
        )
        player_values = {player.stable_key: player.item_value for player in self._state.players}
        self._state.item_value_samples.append(
            ItemValueSample(
                game_time_seconds=game_time,
                blue_total=blue_total,
                red_total=red_total,
                player_values=player_values,
                blue_kills=sum(player.kills or 0 for player in self._state.blue_team.players),
                red_kills=sum(player.kills or 0 for player in self._state.red_team.players),
                blue_objectives=sum(objective_counts["blue"].values()),
                red_objectives=sum(objective_counts["red"].values()),
                blue_objective_breakdown=objective_counts["blue"],
                red_objective_breakdown=objective_counts["red"],
            )
        )
        self._state.item_value_samples = self._state.item_value_samples[-720:]
        self._last_sample_monotonic = now

    def _player_side_lookup(self) -> dict[str, TeamSide]:
        return self._player_side_lookup_from_players(self._state.players)

    def _player_side_lookup_from_players(self, players: list[PlayerState]) -> dict[str, TeamSide]:
        lookup: dict[str, TeamSide] = {}
        for player in players:
            if player.team_side is None:
                continue
            for value in (player.display_name, player.riot_id):
                if value:
                    lookup[value.lower()] = player.team_side
                    if "#" in value:
                        lookup[value.split("#", 1)[0].lower()] = player.team_side
        return lookup


def display_name_from_payload(payload: dict[str, Any]) -> str | None:
    for key in ("riotIdGameName", "displayName", "gameName", "name", "riotId", "summonerName"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            if "#" in value and key in {"riotId", "summonerName"}:
                return value.split("#", 1)[0].strip()
            return value.strip()

    return None


def riot_id_from_payload(payload: dict[str, Any]) -> str | None:
    riot_id = payload.get("riotId") or payload.get("summonerName")
    if isinstance(riot_id, str) and riot_id.strip():
        return riot_id.strip()

    game_name = payload.get("riotIdGameName")
    tag_line = payload.get("riotIdTagLine")
    if isinstance(game_name, str) and game_name.strip():
        if isinstance(tag_line, str) and tag_line.strip():
            return f"{game_name.strip()}#{tag_line.strip()}"
        return game_name.strip()

    return None


def stable_key_from_payload(payload: dict[str, Any], fallback: str) -> str:
    riot_id = riot_id_from_payload(payload)
    if riot_id:
        return f"riotId:{riot_id}"

    for key in ("puuid", "summonerId", "accountId", "participantId", "cellId"):
        value = payload.get(key)
        if value not in (None, "", 0):
            return f"{key}:{value}"
    name = display_name_from_payload(payload)
    if name:
        return f"name:{name}"
    return fallback


def expanded_passive_description(description: str, stat_lines: list[str]) -> str:
    meaningful_stat_lines = [line for line in stat_lines if line != "Cooldown: 0s"]
    if not meaningful_stat_lines:
        return description
    stats = "<br>".join(meaningful_stat_lines)
    if description:
        return f"{description}<br><br><b>Detailed values:</b><br>{stats}"
    return f"<b>Detailed values:</b><br>{stats}"


def player_sort_key(player: PlayerState) -> tuple[int, str]:
    role_order = {
        "TOP": 0,
        "JUNGLE": 1,
        "MIDDLE": 2,
        "MID": 2,
        "BOTTOM": 3,
        "BOT": 3,
        "UTILITY": 4,
        "SUPPORT": 4,
    }
    return (role_order.get((player.position or "").upper(), 99), player.display_name.lower())


def objective_counts_from_events(
    events: list[dict[str, Any]],
    player_side_lookup: dict[str, TeamSide],
) -> dict[str, dict[str, int]]:
    counts = {
        "blue": {"towers": 0, "dragons": 0, "epic": 0, "inhibitors": 0},
        "red": {"towers": 0, "dragons": 0, "epic": 0, "inhibitors": 0},
    }
    for event in events:
        name = str(event.get("EventName") or "")
        side = event_team_side(event, player_side_lookup)
        if side is None:
            continue
        if name in {"TurretKilled", "TurretPlateDestroyed"}:
            counts[side]["towers"] += 1
        elif name in {"DragonKill", "DragonKilled"}:
            counts[side]["dragons"] += 1
        elif name in {"BaronKill", "HeraldKill", "HordeKill", "AtakhanKill"}:
            counts[side]["epic"] += 1
        elif name in {"InhibKilled", "InhibitorKilled"}:
            counts[side]["inhibitors"] += 1
    return counts


def objective_events_from_events(
    events: list[dict[str, Any]],
    player_side_lookup: dict[str, TeamSide],
) -> list[ObjectiveEvent]:
    objective_events = []
    for event in events:
        objective_type = objective_type_from_event(event)
        if objective_type is None:
            continue
        side = event_team_side(event, player_side_lookup)
        if side is None:
            continue
        event_time = float_or_none(event.get("EventTime"))
        if event_time is None:
            continue
        objective_events.append(
            ObjectiveEvent(
                game_time_seconds=event_time,
                team_side=side,
                objective_type=objective_type,
                label=objective_label(event, objective_type),
            )
        )
    return sorted(objective_events, key=lambda objective: objective.game_time_seconds)


def objective_type_from_event(event: dict[str, Any]) -> str | None:
    name = str(event.get("EventName") or "")
    if name in {"TurretKilled", "TurretPlateDestroyed"}:
        return "tower"
    if name in {"DragonKill", "DragonKilled"}:
        dragon_type = str(event.get("DragonType") or event.get("DragonName") or "").lower()
        if dragon_type:
            return f"{dragon_type}_dragon"
        return "dragon"
    if name == "BaronKill":
        return "baron"
    if name == "HeraldKill":
        return "herald"
    if name == "HordeKill":
        return "voidgrub"
    if name == "AtakhanKill":
        return "atakhan"
    if name in {"InhibKilled", "InhibitorKilled"}:
        return "inhibitor"
    return None


def objective_label(event: dict[str, Any], objective_type: str) -> str:
    name = str(event.get("EventName") or objective_type)
    if objective_type.endswith("_dragon"):
        return f"{objective_type.removesuffix('_dragon').title()} Dragon"
    return {
        "tower": "Tower",
        "dragon": "Dragon",
        "baron": "Baron",
        "herald": "Herald",
        "voidgrub": "Voidgrub",
        "atakhan": "Atakhan",
        "inhibitor": "Inhibitor",
    }.get(objective_type, name)


def event_team_side(
    event: dict[str, Any],
    player_side_lookup: dict[str, TeamSide],
) -> TeamSide | None:
    for key in ("KillerTeam", "AcerTeam", "DragonTeam", "Team", "team"):
        side = team_side_from_any(event.get(key))
        if side:
            return side

    killer = event.get("KillerName") or event.get("Killer")
    if isinstance(killer, str):
        normalized = killer.lower()
        if normalized in player_side_lookup:
            return player_side_lookup[normalized]
        if "#" in normalized:
            return player_side_lookup.get(normalized.split("#", 1)[0])

    # Turret/inhibitor events often name the destroyed red/blue structure; award the other side.
    turret = str(event.get("TurretKilled") or event.get("InhibKilled") or "").lower()
    if "t1" in turret or "order" in turret or "blue" in turret:
        return "red"
    if "t2" in turret or "chaos" in turret or "red" in turret:
        return "blue"
    return None


def team_side_from_any(value: Any) -> TeamSide | None:
    if isinstance(value, str):
        normalized = value.lower()
        if normalized in {"order", "blue", "team100", "100"}:
            return "blue"
        if normalized in {"chaos", "red", "team200", "200"}:
            return "red"
    if value == 100:
        return "blue"
    if value == 200:
        return "red"
    return None


def int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def format_number_list(value: Any, require_positive: bool = False) -> str | None:
    if not isinstance(value, list) or not value:
        return None
    numbers = [float_or_none(item) for item in value]
    unique_numbers = []
    for number in numbers:
        if number is None:
            continue
        if require_positive and number <= 0:
            continue
        if number not in unique_numbers:
            unique_numbers.append(number)
    if not unique_numbers:
        return None
    if len(unique_numbers) == 1:
        return format_compact_number(unique_numbers[0])
    return " / ".join(format_compact_number(number) for number in unique_numbers)


def format_series_with_suffix(
    values: list[float],
    suffix: str,
    require_positive: bool = False,
) -> str | None:
    if not values:
        return None
    trimmed = values[1:6] if len(values) > 6 else values[:5]
    if require_positive:
        trimmed = [value for value in trimmed if value > 0]
    if not trimmed:
        return None
    unique = []
    for value in trimmed:
        if value not in unique:
            unique.append(value)
    base = (
        format_compact_number(unique[0])
        if len(unique) == 1
        else "/".join(format_compact_number(value) for value in trimmed)
    )
    return f"{base}{suffix}"


def format_compact_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def spell_candidate_score(
    candidate: tuple[str, dict[str, Any]],
    champion: ChampionData,
    slot: AbilitySlot,
    raw_ability: dict[str, Any] | None = None,
) -> tuple[int, int, int, int, int, int, int, int, int, int]:
    key, value = candidate
    spell = value.get("mSpell") if isinstance(value.get("mSpell"), dict) else {}
    calculations = spell.get("mSpellCalculations")
    data_values = spell.get("DataValues")
    tooltip_data = spell_tooltip_data(spell)
    object_name = string_or_none(value.get("ObjectName")) or string_or_none(
        value.get("mScriptName")
    )
    loc_keys = tooltip_data.get("mLocKeys") if tooltip_data else {}
    leaf = key.rsplit("/", 1)[-1].lower()
    normalized_alias = "".join(
        character.lower() for character in champion.alias if character.isalnum()
    )
    spell_names = spell_names_for_slot(champion, slot)
    raw_placeholders = set(ability_placeholder_names(raw_ability))
    candidate_keys = set(candidate_placeholder_keys(spell))
    placeholder_matches = len(raw_placeholders & candidate_keys)
    placeholder_misses = len(raw_placeholders - candidate_keys)
    source_key_match = source_key_matches_candidate(raw_ability, value, spell)
    return (
        1 if source_key_match else 0,
        placeholder_matches,
        -placeholder_misses,
        1 if loc_key_matches_slot(loc_keys, normalized_alias, slot, spell_names) else 0,
        1 if object_name_matches_slot(object_name, normalized_alias, slot, spell_names) else 0,
        1 if folder_matches_slot(key, slot, normalized_alias, spell_names) else 0,
        1 if isinstance(calculations, dict) and calculations else 0,
        1 if isinstance(data_values, list) and data_values else 0,
        0 if any(marker in leaf for marker in ("buff", "attack", "missile")) else 1,
        -len(key),
    )


def spell_tooltip_data(spell: dict[str, Any]) -> dict[str, Any] | None:
    client_data = spell.get("mClientData")
    if isinstance(client_data, dict) and isinstance(client_data.get("mTooltipData"), dict):
        return client_data["mTooltipData"]
    if isinstance(spell.get("mTooltipData"), dict):
        return spell["mTooltipData"]
    return None


def loc_key_matches_slot(
    loc_keys: dict[str, Any],
    normalized_alias: str,
    slot: AbilitySlot,
    spell_names: set[str] | None = None,
) -> bool:
    if not loc_keys:
        return False
    loc_text = " ".join(str(value) for value in loc_keys.values() if isinstance(value, str))
    normalized = "".join(character.lower() for character in loc_text if character.isalnum())
    if slot == "P":
        return loc_key_matches_passive(loc_keys, normalized_alias)
    return f"spell{normalized_alias}{slot.lower()}" in normalized or any(
        f"spell{spell_name}" in normalized for spell_name in (spell_names or set())
    )


def loc_key_matches_passive(loc_keys: dict[str, Any], normalized_alias: str) -> bool:
    normalized_values = [
        "".join(character.lower() for character in value if character.isalnum())
        for value in loc_keys.values()
        if isinstance(value, str)
    ]
    return any(
        value in {
            f"spell{normalized_alias}pname",
            f"spell{normalized_alias}ptooltip",
            f"spell{normalized_alias}ptooltipextended",
            f"spell{normalized_alias}psummary",
            f"spell{normalized_alias}passivename",
            f"spell{normalized_alias}passivetooltip",
            f"spell{normalized_alias}passivetooltipextended",
            f"spell{normalized_alias}passivesummary",
            f"gamecharacterpassivename{normalized_alias}",
            f"gamecharacterpassivetooltip{normalized_alias}",
            f"gamecharacterpassivedescription{normalized_alias}",
        }
        or value.startswith("generatedtippassive")
        or value.startswith("buff") and "passive" in value
        for value in normalized_values
    )


def object_name_matches_slot(
    object_name: str | None,
    normalized_alias: str,
    slot: AbilitySlot,
    spell_names: set[str] | None = None,
) -> bool:
    if not object_name:
        return False
    normalized = "".join(character.lower() for character in object_name if character.isalnum())
    if slot == "P":
        return normalized in {f"{normalized_alias}p", f"{normalized_alias}passive"}
    return normalized == f"{normalized_alias}{slot.lower()}" or normalized in (spell_names or set())


def folder_matches_slot(
    key: str,
    slot: AbilitySlot,
    normalized_alias: str = "",
    spell_names: set[str] | None = None,
) -> bool:
    lowered = key.lower()
    segments = lowered.split("/")
    normalized_segments = [
        "".join(character.lower() for character in segment if character.isalnum())
        for segment in segments
    ]
    if any(
        segment_matches_spell_name(segment, spell_names or set())
        for segment in normalized_segments
    ):
        return True
    if slot == "P":
        return (
            any(segment.endswith("passiveability") for segment in segments)
            or any(
                segment in passive_folder_segments(normalized_alias)
                for segment in normalized_segments
            )
            or "hemo" in lowered
        )
    slot_lower = slot.lower()
    expected_segments = {f"{slot_lower}ability", f"{slot_lower}wrapperability"}
    if normalized_alias:
        expected_segments.update(
            {
                f"{normalized_alias}{slot_lower}ability",
                f"{normalized_alias}{slot_lower}wrapperability",
            }
        )
    return any(segment in expected_segments for segment in normalized_segments)


def passive_folder_segments(normalized_alias: str = "") -> set[str]:
    segments = {"pability"}
    if normalized_alias:
        segments.add(f"{normalized_alias}pability")
    return segments


def passive_candidate_has_evidence(
    candidate: tuple[str, dict[str, Any]],
    champion: ChampionData,
    raw_ability: dict[str, Any] | None = None,
) -> bool:
    key, value = candidate
    spell = value.get("mSpell") if isinstance(value.get("mSpell"), dict) else {}
    tooltip_data = spell_tooltip_data(spell)
    loc_keys = tooltip_data.get("mLocKeys") if tooltip_data else {}
    object_name = string_or_none(value.get("ObjectName")) or string_or_none(
        value.get("mScriptName")
    )
    normalized_alias = "".join(
        character.lower() for character in champion.alias if character.isalnum()
    )
    return (
        source_key_matches_candidate(raw_ability, value, spell)
        or loc_key_matches_slot(
            loc_keys if isinstance(loc_keys, dict) else {},
            normalized_alias,
            "P",
        )
        or object_name_matches_slot(object_name, normalized_alias, "P")
        or folder_matches_slot(key, "P", normalized_alias)
    )


def spell_names_for_slot(champion: ChampionData, slot: AbilitySlot) -> set[str]:
    if slot == "P":
        return set()
    names = set()
    for spell in champion.spells:
        if not isinstance(spell, dict):
            continue
        if str(spell.get("spellKey") or "").upper() != slot:
            continue
        for key in ("name", "spellName", "scriptName", "mScriptName"):
            value = spell.get(key)
            if isinstance(value, str):
                normalized = "".join(
                    character.lower() for character in value if character.isalnum()
                )
                if normalized:
                    names.add(normalized)
    return names


def segment_matches_spell_name(segment: str, spell_names: set[str]) -> bool:
    for spell_name in spell_names:
        if segment in {
            spell_name,
            f"{spell_name}ability",
            f"{spell_name}wrapperability",
        }:
            return True
        if segment.endswith(f"{spell_name}ability") or segment.endswith(
            f"{spell_name}wrapperability"
        ):
            return True
    return False


def ability_placeholder_names(raw_ability: dict[str, Any] | None) -> list[str]:
    if not isinstance(raw_ability, dict):
        return []
    raw_text = string_or_none(raw_ability.get("dynamicDescription")) or string_or_none(
        raw_ability.get("description")
    )
    names = []
    for placeholder in unresolved_placeholders(raw_text):
        normalized = normalize_ability_formula_key(placeholder)
        if normalized and normalized not in names:
            names.append(normalized)
    return names


def candidate_placeholder_keys(spell: dict[str, Any]) -> list[str]:
    keys = []
    for key in (spell.get("mSpellCalculations") or {}):
        normalized = normalize_ability_formula_key(str(key))
        if normalized:
            keys.append(normalized)
    for raw_value in spell.get("DataValues") or []:
        if not isinstance(raw_value, dict):
            continue
        value = raw_value.get("name")
        if isinstance(value, str) and (normalized := normalize_ability_formula_key(value)):
            keys.append(normalized)
    effects = spell.get("mEffectAmount")
    if isinstance(effects, list):
        for index, raw_effect in enumerate(effects, start=1):
            if isinstance(raw_effect, dict) and raw_effect.get("value"):
                keys.append(normalize_ability_formula_key(f"Effect{index}Amount"))
    return keys


def normalize_ability_formula_key(value: str) -> str:
    value = value.replace("@", "")
    value = re.sub(r"^spell\.", "", value, flags=re.IGNORECASE)
    if ":" in value:
        value = value.split(":", 1)[1]
    value = value.split("*", 1)[0]
    value = value.split(".", 1)[0]
    value = re.sub(r"tooltip", "", value, flags=re.IGNORECASE)
    return "".join(character.lower() for character in value if character.isalnum())


def source_key_matches_candidate(
    raw_ability: dict[str, Any] | None,
    raw_object: dict[str, Any],
    spell: dict[str, Any],
) -> bool:
    if not isinstance(raw_ability, dict):
        return False
    raw_description = raw_ability.get("dynamicDescription")
    source_key = getattr(raw_description, "source_key", None)
    if not isinstance(source_key, str) or not source_key:
        return False
    normalized_source = normalize_ability_source_key(source_key)

    candidates = []
    for value in (raw_object.get("ObjectName"), raw_object.get("mScriptName")):
        if isinstance(value, str):
            candidates.append(value)

    tooltip_data = spell_tooltip_data(spell)
    loc_keys = tooltip_data.get("mLocKeys") if tooltip_data else {}
    if isinstance(loc_keys, dict):
        candidates.extend(str(value) for value in loc_keys.values() if isinstance(value, str))
    object_name = tooltip_data.get("mObjectName") if tooltip_data else None
    if isinstance(object_name, str):
        candidates.append(object_name)

    return any(
        normalize_ability_source_key(candidate) in normalized_source for candidate in candidates
    )


def normalize_ability_source_key(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"^(generatedtip_)?(spell|passive)_", "", lowered)
    lowered = lowered.replace("tooltipcontentextended", "")
    lowered = lowered.replace("tooltipcontent", "")
    lowered = lowered.replace("tooltipextendedbelowline", "")
    lowered = lowered.replace("tooltipextended", "")
    lowered = lowered.replace("tooltip", "")
    lowered = lowered.replace("summary", "")
    lowered = lowered.replace("name", "")
    return "".join(character for character in lowered if character.isalnum())
