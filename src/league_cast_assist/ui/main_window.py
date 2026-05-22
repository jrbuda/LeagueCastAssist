from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from league_cast_assist.config import AppSettings, load_settings, save_settings
from league_cast_assist.data.asset_resolver import AssetResolver
from league_cast_assist.data.simulation import simulated_match_state
from league_cast_assist.data.static_data import StaticDataService
from league_cast_assist.models import AbilityState, ItemState, MatchState, PlayerState
from league_cast_assist.ui.debug_dialog import DebugSimulationDialog
from league_cast_assist.ui.detail_panel import DetailPanel
from league_cast_assist.ui.graph import ItemValueGraphPanel
from league_cast_assist.ui.image_loader import ImageLoader
from league_cast_assist.ui.onboarding import FirstLaunchDialog
from league_cast_assist.ui.settings_dialog import SettingsDialog
from league_cast_assist.ui.widgets import TeamPanel
from league_cast_assist.ui.workers import DataWorker, start_data_worker


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings: AppSettings | None = None,
        start_worker: bool = True,
        show_onboarding: bool = True,
    ) -> None:
        super().__init__()
        self._settings = settings or load_settings()
        self._state = MatchState()
        self._image_loader = ImageLoader()
        self._thread = None
        self._worker: DataWorker | None = None
        self._restart_after_stop = False
        self._debug_simulation_active = False
        self._debug_champion_ids: list[int] = []

        self.setWindowTitle("LeagueCastAssist")
        self.resize(1600, 900)

        self._apply_dark_theme()
        if show_onboarding:
            self._show_first_launch_if_needed()
        self._build_ui()
        self._build_menu()
        if start_worker:
            self._start_worker()

    def closeEvent(self, event) -> None:  # noqa: ANN001
        if self._worker is not None:
            self._worker.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 4, 8, 6)
        root_layout.setSpacing(4)

        header = QHBoxLayout()
        title = QLabel("LeagueCastAssist")
        title.setObjectName("Header")
        header.addWidget(title)
        header.addStretch()

        refresh = QPushButton("Refresh")
        refresh.setToolTip("Poll Riot local APIs immediately")
        refresh.clicked.connect(self._refresh_now)
        header.addWidget(refresh)

        settings = QPushButton("Settings")
        settings.setToolTip("Asset mode and manual name overrides")
        settings.clicked.connect(self._open_settings)
        header.addWidget(settings)
        root_layout.addLayout(header)

        self._loading_label = QLabel("")
        self._loading_label.setObjectName("Muted")
        self._loading_bar = QProgressBar()
        self._loading_bar.setVisible(False)
        self._loading_label.setVisible(False)
        root_layout.addWidget(self._loading_label)
        root_layout.addWidget(self._loading_bar)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
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
        )
        self._red_panel = TeamPanel(
            "Red Team",
            self._image_loader,
            self._show_ability_detail,
            self._show_item_detail,
            self._select_player,
        )
        teams_layout.addWidget(self._blue_panel, stretch=1)
        teams_layout.addWidget(self._red_panel, stretch=1)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(6)
        self._detail_panel = DetailPanel(self._image_loader)
        self._graph_panel = ItemValueGraphPanel()
        right_layout.addWidget(self._detail_panel, stretch=2)
        right_layout.addWidget(self._graph_panel, stretch=1)

        main_splitter.addWidget(teams)
        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(0, 5)
        main_splitter.setStretchFactor(1, 2)
        root_layout.addWidget(main_splitter, stretch=1)

        self.setCentralWidget(root)

        status = QStatusBar()
        status.showMessage("Starting")
        self.setStatusBar(status)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        debug_menu = file_menu.addMenu("Debug")
        simulate_action = QAction("Simulate Champion Setup...", self)
        simulate_action.triggered.connect(self._open_debug_simulation)
        debug_menu.addAction(simulate_action)

        self._stop_debug_action = QAction("Stop Simulation", self)
        self._stop_debug_action.setEnabled(False)
        self._stop_debug_action.triggered.connect(self._stop_debug_simulation)
        debug_menu.addAction(self._stop_debug_action)

        self._show_objectives_graph_action = QAction("Show Objectives Graph", self)
        self._show_objectives_graph_action.setCheckable(True)
        self._show_objectives_graph_action.toggled.connect(
            self._graph_panel.set_debug_objectives_visible
        )
        debug_menu.addAction(self._show_objectives_graph_action)

    def _show_first_launch_if_needed(self) -> None:
        if self._settings.first_launch_complete:
            return

        dialog = FirstLaunchDialog(self._settings, self)
        if dialog.exec():
            self._settings = dialog.settings
            save_settings(self._settings)

    def _start_worker(self) -> None:
        self._restart_after_stop = False
        self._thread, self._worker = start_data_worker(self._settings)
        self._worker.state_updated.connect(self._update_state)
        self._worker.status_updated.connect(self.statusBar().showMessage)
        self._worker.failed.connect(self._show_worker_failure)
        self._worker.finished.connect(self._on_worker_finished)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _restart_worker(self) -> None:
        if self._worker is not None and self._thread is not None and self._thread.isRunning():
            self._restart_after_stop = True
            self.statusBar().showMessage("Restarting data polling")
            self._worker.stop()
            return
        self._start_worker()

    def _refresh_now(self) -> None:
        if self._worker is None:
            self._start_worker()
            return
        self.statusBar().showMessage("Refreshing Riot local data")
        self._worker.request_refresh()

    def _on_worker_finished(self) -> None:
        pass

    def _on_thread_finished(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None
        if self._restart_after_stop:
            self._start_worker()

    def _update_state(self, state: MatchState) -> None:
        if self._debug_simulation_active and state.source != "debug":
            return
        self._state = state
        self._blue_panel.update_team(state.blue_team)
        self._red_panel.update_team(state.red_team)
        self._graph_panel.update_state(state)
        self._update_loading(state)
        self.statusBar().showMessage(state.status)

    def _update_loading(self, state: MatchState) -> None:
        self._loading_label.setVisible(state.loading_active)
        self._loading_bar.setVisible(state.loading_active)
        if not state.loading_active:
            return
        self._loading_label.setText(state.loading_message or "Loading")
        if state.loading_total > 0:
            self._loading_bar.setRange(0, state.loading_total)
            self._loading_bar.setValue(state.loading_current)
        else:
            self._loading_bar.setRange(0, 0)

    def _show_ability_detail(self, player: PlayerState, ability: AbilityState) -> None:
        self._detail_panel.show_ability(player, ability)
        self._graph_panel.set_selected_player(player)

    def _show_item_detail(self, player: PlayerState, item: ItemState) -> None:
        self._detail_panel.show_item(player, item)
        self._graph_panel.set_selected_player(player)

    def _select_player(self, player: PlayerState) -> None:
        self._graph_panel.set_selected_player(player)
        self.statusBar().showMessage(f"Selected {player.display_name}")

    def _show_worker_failure(self, traceback_text: str) -> None:
        QMessageBox.critical(self, "Data worker failed", traceback_text)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self._state, self)
        if not dialog.exec():
            return

        self._settings = dialog.settings
        save_settings(self._settings)
        self._restart_worker()

    def _open_debug_simulation(self) -> None:
        static_data = StaticDataService(
            version=self._settings.assets.version,
            download_assets=False,
        )
        try:
            asyncio.run(static_data.ensure_core_data())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Debug data unavailable", str(exc))
            return

        dialog = DebugSimulationDialog(
            list(static_data.champion_summary().values()),
            self._debug_champion_ids,
            self,
        )
        if not dialog.exec():
            return

        self._debug_champion_ids = dialog.selected_champion_ids()
        self._start_debug_simulation()

    def _start_debug_simulation(self) -> None:
        self._debug_simulation_active = True
        self._stop_debug_action.setEnabled(True)
        if self._worker is not None:
            self._worker.stop()

        static_data = StaticDataService(
            version=self._settings.assets.version,
            download_assets=self._settings.assets.mode == "local",
        )
        asset_resolver = AssetResolver(
            local_assets=self._settings.assets.mode == "local",
            version=self._settings.assets.version,
        )
        try:
            state = asyncio.run(
                simulated_match_state(static_data, asset_resolver, self._debug_champion_ids)
            )
        except Exception as exc:  # noqa: BLE001
            self._debug_simulation_active = False
            self._stop_debug_action.setEnabled(False)
            QMessageBox.critical(self, "Debug simulation failed", str(exc))
            return

        self._update_state(state)

    def _stop_debug_simulation(self) -> None:
        self._debug_simulation_active = False
        self._stop_debug_action.setEnabled(False)
        self.statusBar().showMessage("Debug simulation stopped")
        if self._worker is None:
            self._start_worker()

    def _apply_dark_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #101319;
                color: #e6eaf2;
                font-family: Segoe UI, Arial, sans-serif;
                font-size: 13px;
            }
            QLabel#Header {
                font-size: 20px;
                font-weight: 700;
                padding: 4px 2px;
            }
            QLabel#SectionTitle {
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#PlayerName {
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#AbilityName {
                font-size: 9px;
            }
            QLabel#Muted {
                color: #9aa4b2;
            }
            QLabel#DetailTitle {
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#DetailDescription {
                border: 1px solid #2d3440;
                border-radius: 8px;
                padding: 8px;
                background: #141922;
            }
            QLabel#LargeIcon, QLabel#Portrait {
                border: 1px solid #343d4c;
                border-radius: 6px;
                background: #0d1016;
            }
            QFrame#TeamPanel, QFrame#PlayerCard, QFrame#DetailPanel, QFrame#GraphPanel {
                border: 1px solid #2d3440;
                border-radius: 8px;
                background: #171b24;
            }
            QFrame#PlayerCard {
                padding: 3px;
            }
            QPushButton {
                background: #232a35;
                border: 1px solid #394252;
                border-radius: 6px;
                padding: 4px 7px;
            }
            QPushButton:hover {
                background: #2d3644;
            }
            QScrollArea {
                border: none;
            }
            QSplitter::handle {
                background: #232a35;
            }
            """
        )
