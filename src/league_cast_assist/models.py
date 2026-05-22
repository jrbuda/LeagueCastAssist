from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TeamSide = Literal["blue", "red"]
AbilitySlot = Literal["P", "Q", "W", "E", "R"]


class AbilityState(BaseModel):
    slot: AbilitySlot
    name: str = "Unknown Ability"
    icon: str | None = None
    short_description: str | None = None
    full_description: str | None = None
    tooltip_html: str = ""
    stat_lines: list[str] = Field(default_factory=list)
    cooldown: str | None = None
    cost: str | None = None
    range: str | None = None


class ItemState(BaseModel):
    item_id: int
    name: str = "Unknown Item"
    icon: str | None = None
    description: str | None = None
    tooltip_html: str = ""
    total_cost: int | None = None
    slot: int | None = None
    count: int = 1


class PlayerState(BaseModel):
    stable_key: str
    display_name: str = "Unknown Player"
    riot_id: str | None = None
    team_side: TeamSide | None = None
    position: str | None = None
    champion_id: int | None = None
    champion_name: str | None = None
    champion_icon: str | None = None
    abilities: list[AbilityState] = Field(default_factory=list)
    items: list[ItemState] = Field(default_factory=list)
    item_value: int = 0
    level: int | None = None
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    creep_score: int | None = None
    ward_score: float | None = None


class TeamState(BaseModel):
    side: TeamSide
    display_name: str
    players: list[PlayerState] = Field(default_factory=list)


class ObjectiveEvent(BaseModel):
    game_time_seconds: float
    team_side: TeamSide
    objective_type: str
    label: str


class ItemValueSample(BaseModel):
    game_time_seconds: float
    blue_total: int
    red_total: int
    player_values: dict[str, int] = Field(default_factory=dict)
    blue_kills: int = 0
    red_kills: int = 0
    blue_objectives: int = 0
    red_objectives: int = 0
    blue_objective_breakdown: dict[str, int] = Field(default_factory=dict)
    red_objective_breakdown: dict[str, int] = Field(default_factory=dict)


class MatchState(BaseModel):
    phase: str = "Disconnected"
    status: str = "League client not connected"
    source: str = "none"
    game_time_seconds: float | None = None
    blue_team: TeamState = Field(
        default_factory=lambda: TeamState(side="blue", display_name="Blue Team")
    )
    red_team: TeamState = Field(
        default_factory=lambda: TeamState(side="red", display_name="Red Team")
    )
    item_value_samples: list[ItemValueSample] = Field(default_factory=list)
    objective_events: list[ObjectiveEvent] = Field(default_factory=list)
    loading_active: bool = False
    loading_message: str = ""
    loading_current: int = 0
    loading_total: int = 0

    @property
    def players(self) -> list[PlayerState]:
        return [*self.blue_team.players, *self.red_team.players]
