from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from league_cast_assist.config import AppSettings
from league_cast_assist.models import AbilityState, ItemState, MatchState, PlayerState
from league_cast_assist.ui.detail_panel import DetailPanel
from league_cast_assist.ui.graph import ItemValueGraphPanel
from league_cast_assist.ui.image_loader import ImageLoader
from league_cast_assist.ui.widgets import RoleComparisonPanel, TeamPanel


class MatchPage(QWidget):
    """Team-vs-team champion screen. Owns no Riot polling; state is pushed in."""

    page_title = "Match"
    status_message = Signal(str)

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MatchPage")
        self._settings = settings
        self._state = MatchState()
        self._image_loader = ImageLoader()
        self._build_ui()

    # ── construction ───────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 6, 0, 0)
        root_layout.setSpacing(4)

        self._loading_label = QLabel("")
        self._loading_label.setObjectName("Muted")
        self._loading_bar = QProgressBar()
        self._loading_bar.setVisible(False)
        self._loading_label.setVisible(False)
        root_layout.addWidget(self._loading_label)
        root_layout.addWidget(self._loading_bar)

        main_splitter = QSplitter(Qt.Orientation.Vertical)
        teams = QWidget()
        teams_layout = QVBoxLayout(teams)
        teams_layout.setContentsMargins(0, 0, 0, 0)
        teams_layout.setSpacing(6)

        self._blue_panel = TeamPanel(
            "Blue Team",
            self._image_loader,
            self._show_ability_detail,
            self._show_item_detail,
            self._select_player,
            self._show_rune_detail,
            hover_to_describe=self._settings.ui.hover_to_describe,
        )
        self._red_panel = TeamPanel(
            "Red Team",
            self._image_loader,
            self._show_ability_detail,
            self._show_item_detail,
            self._select_player,
            self._show_rune_detail,
            hover_to_describe=self._settings.ui.hover_to_describe,
        )
        self._comparison_panel = RoleComparisonPanel()
        teams_layout.addWidget(self._blue_panel)
        teams_layout.addWidget(self._comparison_panel)
        teams_layout.addWidget(self._red_panel)
        teams_layout.addStretch(1)

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 4, 0, 0)
        bottom_layout.setSpacing(6)
        self._detail_panel = DetailPanel(self._image_loader)
        self._graph_panel = ItemValueGraphPanel()
        bottom_layout.addWidget(self._detail_panel, stretch=1)
        bottom_layout.addWidget(self._graph_panel, stretch=2)

        main_splitter.addWidget(teams)
        main_splitter.addWidget(bottom)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([420, 340])
        root_layout.addWidget(main_splitter, stretch=1)

    # ── state ──────────────────────────────────────────────────────────────
    @property
    def state(self) -> MatchState:
        return self._state

    def update_state(self, state: MatchState) -> None:
        was_loading = self._state.loading_active
        self._state = state
        self._blue_panel.update_team(state.blue_team)
        self._comparison_panel.update_teams(state.blue_team, state.red_team)
        self._red_panel.update_team(state.red_team)
        self._graph_panel.update_state(state)
        self._update_loading(state)
        if was_loading and not state.loading_active:
            self.refresh_images()

    def refresh_images(self) -> None:
        for player in self._state.players:
            self._image_loader.forget(player.champion_icon)
            for ability in player.abilities:
                self._image_loader.forget(ability.icon)
            for item in player.items:
                self._image_loader.forget(item.icon)
        self._blue_panel.force_next_update()
        self._red_panel.force_next_update()
        self._blue_panel.update_team(self._state.blue_team)
        self._comparison_panel.update_teams(self._state.blue_team, self._state.red_team)
        self._red_panel.update_team(self._state.red_team)
        self._detail_panel.refresh_icon()

    def set_hover_to_describe(self, enabled: bool) -> None:
        self._blue_panel.set_hover_to_describe(enabled)
        self._red_panel.set_hover_to_describe(enabled)
        self._blue_panel.update_team(self._state.blue_team)
        self._red_panel.update_team(self._state.red_team)

    def set_debug_objectives_visible(self, visible: bool) -> None:
        self._graph_panel.set_debug_objectives_visible(visible)

    def _update_loading(self, state: MatchState) -> None:
        self._loading_label.setVisible(state.loading_active)
        self._loading_bar.setVisible(state.loading_active)
        if not state.loading_active:
            self._loading_label.clear()
            self._loading_bar.setValue(0)
            return
        self._loading_label.setText(state.loading_message or "Loading")
        if state.loading_total > 0:
            self._loading_bar.setRange(0, state.loading_total)
            self._loading_bar.setValue(state.loading_current)
        else:
            self._loading_bar.setRange(0, 0)

    # ── detail panel wiring ────────────────────────────────────────────────
    def _show_ability_detail(self, player: PlayerState, ability: AbilityState) -> None:
        self._detail_panel.show_ability(player, ability)
        self._graph_panel.set_selected_player(player)

    def _show_item_detail(self, player: PlayerState, item: ItemState) -> None:
        self._detail_panel.show_item(player, item)
        self._graph_panel.set_selected_player(player)

    def _select_player(self, player: PlayerState) -> None:
        self._graph_panel.set_selected_player(player)
        self.status_message.emit(f"Selected {player.display_name}")

    def _show_rune_detail(self, player: PlayerState) -> None:
        self._detail_panel.show_rune(player)
        self._graph_panel.set_selected_player(player)
