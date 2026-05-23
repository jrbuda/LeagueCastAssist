from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from league_cast_assist.models import AbilityState, ItemState, PlayerState, TeamState
from league_cast_assist.ui.image_loader import ImageLoader

AbilityCallback = Callable[[PlayerState, AbilityState], None]
ItemCallback = Callable[[PlayerState, ItemState], None]
PlayerCallback = Callable[[PlayerState], None]

ROLE_ORDER = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
ROLE_LABELS = {
    "TOP": "Top",
    "JUNGLE": "Jungle",
    "MIDDLE": "Mid",
    "BOTTOM": "Bot",
    "UTILITY": "Support",
}
ROLE_ALIASES = {
    "TOP": "TOP",
    "JUNGLE": "JUNGLE",
    "MIDDLE": "MIDDLE",
    "MID": "MIDDLE",
    "BOTTOM": "BOTTOM",
    "BOT": "BOTTOM",
    "UTILITY": "UTILITY",
    "SUPPORT": "UTILITY",
}


@dataclass(frozen=True)
class RoleComparison:
    role: str
    blue_player: PlayerState
    red_player: PlayerState

    @property
    def lead_amount(self) -> int:
        return abs(self.blue_player.item_value - self.red_player.item_value)

    @property
    def lead_side(self) -> str | None:
        if self.blue_player.item_value > self.red_player.item_value:
            return "blue"
        if self.red_player.item_value > self.blue_player.item_value:
            return "red"
        return None


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class TeamPanel(QFrame):
    def __init__(
        self,
        side_name: str,
        image_loader: ImageLoader,
        ability_callback: AbilityCallback,
        item_callback: ItemCallback,
        player_callback: PlayerCallback,
    ) -> None:
        super().__init__()
        self.setObjectName("TeamPanel")
        self._image_loader = image_loader
        self._ability_callback = ability_callback
        self._item_callback = item_callback
        self._player_callback = player_callback
        self._side = "blue" if "blue" in side_name.lower() else "red"
        self._last_signature: tuple | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(3)
        self._title = QLabel(side_name)
        self._title.setObjectName("SectionTitle")
        layout.addWidget(self._title)

        self._content = QWidget()
        self._content_layout = QHBoxLayout(self._content)
        self._content_layout.setSpacing(5)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._content, stretch=1)

        self.update_team(TeamState(side=self._side, display_name=side_name))

    def update_team(self, team: TeamState) -> None:
        signature = team_signature(team)
        if signature == self._last_signature:
            self._title.setText(team.display_name)
            return
        self._last_signature = signature

        self._title.setText(team.display_name)
        clear_layout(self._content_layout)
        for player in team.players:
            self._content_layout.addWidget(
                PlayerCard(
                    player=player,
                    image_loader=self._image_loader,
                    ability_callback=self._ability_callback,
                    item_callback=self._item_callback,
                    player_callback=self._player_callback,
                ),
                stretch=1,
            )
        if not team.players:
            placeholder = QLabel("Waiting for players")
            placeholder.setObjectName("Muted")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._content_layout.addWidget(placeholder)

    def force_next_update(self) -> None:
        self._last_signature = None


class RoleComparisonPanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("RoleComparisonPanel")
        self._last_signature: tuple | None = None

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(6, 0, 6, 0)
        self._layout.setSpacing(5)
        self.setVisible(False)

    def update_teams(self, blue_team: TeamState, red_team: TeamState) -> None:
        signature = role_comparison_signature(blue_team, red_team)
        if signature == self._last_signature:
            return
        self._last_signature = signature

        clear_layout(self._layout)
        comparisons = role_comparisons_by_role(blue_team, red_team)
        self.setVisible(bool(comparisons))

        for role in ROLE_ORDER:
            comparison = comparisons.get(role)
            if comparison is None:
                placeholder = QWidget()
                placeholder.setFixedHeight(36)
                self._layout.addWidget(placeholder, stretch=1)
                continue
            self._layout.addWidget(RoleComparisonMarker(comparison), stretch=1)


class RoleComparisonMarker(QFrame):
    def __init__(self, comparison: RoleComparison) -> None:
        super().__init__()
        self.setObjectName("RoleComparisonMarker")
        self.setFixedHeight(36)
        self.setToolTip(role_comparison_tooltip(comparison))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(0)

        top_arrow = QLabel("^" if comparison.lead_side == "blue" else "")
        top_arrow.setObjectName("ComparisonArrowBlue")
        top_arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)

        amount_text = (
            format_gold_amount(comparison.lead_amount) if comparison.lead_amount else "Even"
        )
        amount = QLabel(amount_text)
        amount.setObjectName("ComparisonAmount")
        amount.setAlignment(Qt.AlignmentFlag.AlignCenter)

        bottom_arrow = QLabel("v" if comparison.lead_side == "red" else "")
        bottom_arrow.setObjectName("ComparisonArrowRed")
        bottom_arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(top_arrow)
        layout.addWidget(amount)
        layout.addWidget(bottom_arrow)


class PlayerCard(QFrame):
    def __init__(
        self,
        player: PlayerState,
        image_loader: ImageLoader,
        ability_callback: AbilityCallback,
        item_callback: ItemCallback,
        player_callback: PlayerCallback,
    ) -> None:
        super().__init__()
        self._player = player
        self._image_loader = image_loader
        self._portrait_source = player.champion_icon
        self.setObjectName("PlayerCard")

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(3)

        header = QVBoxLayout()
        header.setSpacing(2)

        self._portrait = ClickableLabel()
        self._portrait.setFixedSize(34, 34)
        self._portrait.setObjectName("Portrait")
        self._portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._portrait.clicked.connect(lambda: player_callback(player))
        header.addWidget(self._portrait, alignment=Qt.AlignmentFlag.AlignCenter)

        names = QVBoxLayout()
        names.setSpacing(1)
        player_name = QLabel(player.display_name)
        player_name.setObjectName("PlayerName")
        player_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        player_name.setWordWrap(True)
        champion_name = QLabel(player.champion_name or "Champion pending")
        champion_name.setObjectName("Muted")
        champion_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        champion_name.setWordWrap(True)
        stats = QLabel(self._stats_text(player))
        stats.setObjectName("Muted")
        stats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stats.setWordWrap(True)
        names.addWidget(player_name)
        names.addWidget(champion_name)
        names.addWidget(stats)
        header.addLayout(names)
        root.addLayout(header)

        ability_grid = QGridLayout()
        ability_grid.setHorizontalSpacing(3)
        ability_grid.setVerticalSpacing(1)
        for row, ability in enumerate(player.abilities):
            ability_button = AbilityButton(player, ability, image_loader)
            ability_button.clicked.connect(
                lambda _=False, p=player, a=ability: ability_callback(p, a)
            )
            ability_grid.addWidget(ability_button, row, 0)
            name = QLabel(f"{ability.slot}: {ability.name}")
            name.setWordWrap(True)
            name.setObjectName("AbilityName")
            ability_grid.addWidget(name, row, 1)
        if not player.abilities:
            ability_grid.addWidget(QLabel("Abilities pending"), 0, 0, 1, 2)
        root.addLayout(ability_grid)

        item_row = QHBoxLayout()
        item_row.setSpacing(2)
        for item in player.items[:7]:
            item_button = ItemIcon(player, item, image_loader)
            item_button.clicked.connect(lambda _=False, p=player, i=item: item_callback(p, i))
            item_row.addWidget(item_button)
        item_row.addStretch()
        root.addLayout(item_row)

        self._image_loader.loaded.connect(self._on_image_loaded)
        self._load_portrait()

    def _load_portrait(self) -> None:
        if not self._portrait_source:
            self._portrait.setText("?")
            self._portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return
        pixmap = self._image_loader.load(self._portrait_source)
        if pixmap is not None:
            self._set_portrait(pixmap)

    def _on_image_loaded(self, source: str, pixmap: QPixmap) -> None:
        if source == self._portrait_source:
            self._set_portrait(pixmap)

    def _set_portrait(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        self._portrait.setText("")
        self._portrait.setPixmap(
            pixmap.scaled(
                self._portrait.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _stats_text(self, player: PlayerState) -> str:
        parts = []
        if player.position:
            parts.append(format_position(player.position))
        if player.level is not None:
            parts.append(f"Lv {player.level}")
        if player.kills is not None and player.deaths is not None and player.assists is not None:
            parts.append(f"{player.kills}/{player.deaths}/{player.assists}")
        if player.creep_score is not None:
            parts.append(f"{player.creep_score} CS")
        return " | ".join(parts) if parts else "Stats pending"


class AbilityButton(QPushButton):
    def __init__(
        self,
        player: PlayerState,
        ability: AbilityState,
        image_loader: ImageLoader,
    ) -> None:
        super().__init__(ability.slot)
        self._source = ability.icon
        self._image_loader = image_loader
        self.setFixedSize(18, 18)
        self.setToolTip(f"{player.champion_name or 'Champion'} - {ability.name}")
        self._image_loader.loaded.connect(self._on_image_loaded)
        self._load_icon()

    def _load_icon(self) -> None:
        pixmap = self._image_loader.load(self._source)
        if pixmap is not None:
            self._set_icon(pixmap)

    def _on_image_loaded(self, source: str, pixmap: QPixmap) -> None:
        if source == self._source:
            self._set_icon(pixmap)

    def _set_icon(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        self.setText("")
        self.setIcon(pixmap_to_icon(pixmap))
        self.setIconSize(self.size())


class ItemIcon(QPushButton):
    def __init__(self, player: PlayerState, item: ItemState, image_loader: ImageLoader) -> None:
        super().__init__("")
        self._source = item.icon
        self._image_loader = image_loader
        self.setFixedSize(18, 18)
        cost = f" ({item.total_cost})" if item.total_cost is not None else ""
        self.setToolTip(f"{item.name}{cost}")
        self._image_loader.loaded.connect(self._on_image_loaded)
        self._load_icon()

    def _load_icon(self) -> None:
        pixmap = self._image_loader.load(self._source)
        if pixmap is not None:
            self._set_icon(pixmap)

    def _on_image_loaded(self, source: str, pixmap: QPixmap) -> None:
        if source == self._source:
            self._set_icon(pixmap)

    def _set_icon(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        self.setIcon(pixmap_to_icon(pixmap))
        self.setIconSize(self.size())


def pixmap_to_icon(pixmap: QPixmap) -> QIcon:
    return QIcon(pixmap)


def format_position(position: str) -> str:
    normalized = normalize_role(position)
    if normalized:
        return ROLE_LABELS[normalized]
    return position.title()


def format_gold_amount(value: int) -> str:
    if value >= 1000:
        formatted = f"{value / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{formatted}k"
    return str(value)


def normalize_role(position: str | None) -> str | None:
    if not position:
        return None
    return ROLE_ALIASES.get(position.upper())


def role_comparisons_by_role(
    blue_team: TeamState,
    red_team: TeamState,
) -> dict[str, RoleComparison]:
    blue_by_role = players_by_role(blue_team.players)
    red_by_role = players_by_role(red_team.players)
    return {
        role: RoleComparison(role, blue_by_role[role], red_by_role[role])
        for role in ROLE_ORDER
        if role in blue_by_role and role in red_by_role
    }


def players_by_role(players: list[PlayerState]) -> dict[str, PlayerState]:
    players_by_role = {}
    for player in players:
        role = normalize_role(player.position)
        if role and role not in players_by_role:
            players_by_role[role] = player
    return players_by_role


def role_comparison_tooltip(comparison: RoleComparison) -> str:
    role = ROLE_LABELS[comparison.role]
    blue_value = format_gold_amount(comparison.blue_player.item_value)
    red_value = format_gold_amount(comparison.red_player.item_value)
    if comparison.lead_side == "blue":
        leader = comparison.blue_player.display_name
    elif comparison.lead_side == "red":
        leader = comparison.red_player.display_name
    else:
        leader = "Even"

    return (
        f"{role} effective gold\n"
        f"Blue: {comparison.blue_player.display_name} ({blue_value})\n"
        f"Red: {comparison.red_player.display_name} ({red_value})\n"
        f"Lead: {leader} by {format_gold_amount(comparison.lead_amount)}"
    )


def role_comparison_signature(blue_team: TeamState, red_team: TeamState) -> tuple:
    return (
        tuple(role_player_signature(player) for player in blue_team.players),
        tuple(role_player_signature(player) for player in red_team.players),
    )


def role_player_signature(player: PlayerState) -> tuple:
    return (
        player.stable_key,
        player.display_name,
        player.position,
        player.item_value,
    )


def clear_layout(layout: QHBoxLayout | QVBoxLayout | QGridLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def team_signature(team: TeamState) -> tuple:
    return (
        team.display_name,
        tuple(
            (
                player.stable_key,
                player.display_name,
                player.champion_id,
                player.level,
                player.kills,
                player.deaths,
                player.assists,
                player.creep_score,
                player.item_value,
                tuple(
                    (item.item_id, item.count, item.slot, item.total_cost)
                    for item in player.items
                ),
                tuple(
                    (
                        ability.slot,
                        ability.name,
                        ability.icon,
                        ability.cooldown,
                        ability.cost,
                        ability.range,
                        tuple(ability.stat_lines),
                        ability.full_description,
                    )
                    for ability in player.abilities
                ),
            )
            for player in team.players
        ),
    )
