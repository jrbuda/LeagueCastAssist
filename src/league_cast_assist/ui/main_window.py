from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from league_cast_assist import __version__
from league_cast_assist.config import AppSettings, load_settings, save_settings
from league_cast_assist.models import AbilityState, ItemState, MatchState, PlayerState
from league_cast_assist.resources import app_icon_path
from league_cast_assist.ui.debug_dialog import DebugSimulationDialog
from league_cast_assist.ui.detail_panel import DetailPanel
from league_cast_assist.ui.graph import ItemValueGraphPanel
from league_cast_assist.ui.image_loader import ImageLoader
from league_cast_assist.ui.onboarding import FirstLaunchDialog
from league_cast_assist.ui.settings_dialog import SettingsDialog
from league_cast_assist.ui.widgets import RoleComparisonPanel, TeamPanel
from league_cast_assist.ui.workers import (
    DataWorker,
    DebugDataWorker,
    DebugSimulationWorker,
    ReleaseNotesWorker,
    UpdateCheckWorker,
    UpdateDownloadWorker,
    start_data_worker,
    start_debug_data_worker,
    start_debug_simulation_worker,
    start_release_notes_worker,
    start_update_check_worker,
    start_update_download_worker,
)
from league_cast_assist.update import (
    GITHUB_OWNER,
    GITHUB_REPO,
    UpdateCheckResult,
    UpdateRelease,
    can_install_downloaded_update,
    install_update_after_exit,
    is_frozen_app,
)


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
        self._closing = False
        self._restart_after_stop = False
        self._debug_simulation_active = False
        self._debug_data_thread = None
        self._debug_data_worker: DebugDataWorker | None = None
        self._debug_simulation_thread = None
        self._debug_simulation_worker: DebugSimulationWorker | None = None
        self._update_check_thread = None
        self._update_check_worker: UpdateCheckWorker | None = None
        self._update_download_thread = None
        self._update_download_worker: UpdateDownloadWorker | None = None
        self._manual_update_check = False
        self._release_notes_thread = None
        self._release_notes_worker: ReleaseNotesWorker | None = None
        self._debug_champion_ids: list[int] = []
        self._debug_item_ids_by_player: list[list[int]] = []

        self.setWindowTitle("LeagueCastAssist")
        icon_path = app_icon_path()
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1050, 800)

        self._apply_dark_theme()
        _was_first_launch_complete = self._settings.first_launch_complete
        if show_onboarding:
            self._show_first_launch_if_needed()
        self._build_ui()
        self._build_menu()
        self.menuBar().hide()
        if start_worker:
            self._start_worker()
        if self._settings.updates.auto_check and is_frozen_app():
            QTimer.singleShot(2500, self._check_for_updates_auto)
        if show_onboarding and self._settings.last_seen_version != __version__:
            self._settings.last_seen_version = __version__
            save_settings(self._settings)
            if _was_first_launch_complete:
                QTimer.singleShot(800, self._check_for_whats_new)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        if self._closing:
            if self._has_running_thread():
                event.ignore()
                return
            super().closeEvent(event)
            return

        self._closing = True
        self._cancel_workers_for_close()
        running_threads = self._running_threads()
        if running_threads:
            for thread in running_threads:
                thread.finished.connect(self.close)
            self.statusBar().showMessage("Stopping background work")
            event.ignore()
            return

        self._closing = False
        super().closeEvent(event)

    def _has_running_thread(self) -> bool:
        return bool(self._running_threads())

    def _running_threads(self):  # noqa: ANN202
        return [
            thread
            for thread in (
                self._thread,
                self._debug_data_thread,
                self._debug_simulation_thread,
                self._update_check_thread,
                self._update_download_thread,
                self._release_notes_thread,
            )
            if thread is not None and thread.isRunning()
        ]

    def _cancel_workers_for_close(self) -> None:
        if self._debug_data_worker is not None:
            self._debug_data_worker.cancel()
        if self._debug_simulation_worker is not None:
            self._debug_simulation_worker.cancel()
        if self._update_check_worker is not None:
            self._update_check_worker.cancel()
        if self._update_download_worker is not None:
            self._update_download_worker.cancel()
        if self._release_notes_worker is not None:
            self._release_notes_worker.cancel()
        if self._worker is not None:
            self._worker.stop()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 4, 8, 6)
        root_layout.setSpacing(4)

        header = QHBoxLayout()
        self._file_button = QPushButton("File")
        header.addWidget(self._file_button)

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

        self.setCentralWidget(root)

        status = QStatusBar()
        status.showMessage("Starting")
        self.setStatusBar(status)

    def _build_menu(self) -> None:
        file_menu = QMenu(self)
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        update_action = QAction("Check for Updates", self)
        update_action.triggered.connect(self._check_for_updates_manual)
        file_menu.addAction(update_action)

        self._auto_update_action = QAction("Check for Updates Automatically", self)
        self._auto_update_action.setCheckable(True)
        self._auto_update_action.setChecked(self._settings.updates.auto_check)
        self._auto_update_action.toggled.connect(self._set_auto_updates_enabled)
        file_menu.addAction(self._auto_update_action)

        about_action = QAction(f"About LeagueCastAssist {__version__}", self)
        about_action.triggered.connect(self._show_about)
        file_menu.addAction(about_action)

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
        self._file_button.setMenu(file_menu)

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
        self._worker.patch_update_available.connect(self._show_patch_update_available)
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

    def _check_for_updates_auto(self) -> None:
        if self._closing or self._update_check_thread is not None:
            return
        self._start_update_check(manual=False)

    def _check_for_updates_manual(self) -> None:
        if self._update_check_thread is not None:
            self._manual_update_check = True
            self.statusBar().showMessage("Already checking for app updates")
            return
        self._start_update_check(manual=True)

    def _start_update_check(self, manual: bool) -> None:
        self._manual_update_check = manual
        if manual:
            self.statusBar().showMessage("Checking for app updates")
        self._update_check_thread, self._update_check_worker = start_update_check_worker(
            __version__
        )
        self._update_check_worker.update_checked.connect(self._handle_update_check_result)
        self._update_check_worker.failed.connect(self._show_update_check_failure)
        self._update_check_worker.finished.connect(self._on_update_check_finished)
        self._update_check_thread.start()

    def _on_worker_finished(self) -> None:
        self._refresh_visible_images()

    def _on_thread_finished(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None
        if self._closing:
            self.close()
        elif self._debug_simulation_active:
            return
        elif self._restart_after_stop:
            self._start_worker()

    def _update_state(self, state: MatchState) -> None:
        if self._debug_simulation_active and state.source != "debug":
            return
        was_loading = self._state.loading_active
        self._state = state
        self._blue_panel.update_team(state.blue_team)
        self._comparison_panel.update_teams(state.blue_team, state.red_team)
        self._red_panel.update_team(state.red_team)
        self._graph_panel.update_state(state)
        self._update_loading(state)
        if was_loading and not state.loading_active:
            self._refresh_visible_images()
        self.statusBar().showMessage(state.status)

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

    def _show_ability_detail(self, player: PlayerState, ability: AbilityState) -> None:
        self._detail_panel.show_ability(player, ability)
        self._graph_panel.set_selected_player(player)

    def _show_item_detail(self, player: PlayerState, item: ItemState) -> None:
        self._detail_panel.show_item(player, item)
        self._graph_panel.set_selected_player(player)

    def _select_player(self, player: PlayerState) -> None:
        self._graph_panel.set_selected_player(player)
        self.statusBar().showMessage(f"Selected {player.display_name}")

    def _show_rune_detail(self, player: PlayerState) -> None:
        self._detail_panel.show_rune(player)
        self._graph_panel.set_selected_player(player)

    def _show_worker_failure(self, traceback_text: str) -> None:
        QMessageBox.critical(self, "Data worker failed", traceback_text)

    def _handle_update_check_result(self, result: UpdateCheckResult) -> None:
        if self._closing:
            return
        if result.update_available and result.release is not None:
            self._prompt_for_app_update(result.release)
            return
        if self._manual_update_check:
            latest = result.latest_version or "unknown"
            message = f"LeagueCastAssist {__version__} is up to date."
            if result.reason:
                message = f"No installable update found.\n\n{result.reason}"
            QMessageBox.information(
                self,
                "No Update Available",
                f"{message}\n\nLatest release: {latest}",
            )

    def _prompt_for_app_update(self, release: UpdateRelease) -> None:
        if self._update_download_thread is not None:
            return

        message = (
            f"LeagueCastAssist {release.version} is available.\n\n"
            f"Installed version: {__version__}\n"
            f"Release: {release.tag_name}\n\n"
            "Download and install this update now? The app will restart after installation."
        )
        response = QMessageBox.question(
            self,
            "Update Available",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if response != QMessageBox.StandardButton.Yes:
            return

        self._start_update_download(release)

    def _start_update_download(self, release: UpdateRelease) -> None:
        self.statusBar().showMessage("Downloading app update")
        self._loading_label.setVisible(True)
        self._loading_label.setText("Downloading app update")
        self._loading_bar.setVisible(True)
        self._loading_bar.setRange(0, release.asset.size if release.asset.size > 0 else 0)
        self._loading_bar.setValue(0)
        self._update_download_thread, self._update_download_worker = start_update_download_worker(
            release
        )
        self._update_download_worker.progress_updated.connect(self._update_app_download_progress)
        self._update_download_worker.update_downloaded.connect(self._install_downloaded_update)
        self._update_download_worker.failed.connect(self._show_update_download_failure)
        self._update_download_worker.finished.connect(self._on_update_download_finished)
        self._update_download_thread.start()

    def _update_app_download_progress(self, message: str, current: int, total: int) -> None:
        self._loading_label.setVisible(True)
        self._loading_label.setText(message)
        self._loading_bar.setVisible(True)
        if total > 0:
            self._loading_bar.setRange(0, total)
            self._loading_bar.setValue(min(current, total))
        else:
            self._loading_bar.setRange(0, 0)
        self.statusBar().showMessage(message)

    def _install_downloaded_update(self, downloaded_path: Path) -> None:
        if not can_install_downloaded_update():
            QMessageBox.information(
                self,
                "Update Downloaded",
                f"The update was downloaded to:\n{downloaded_path}\n\n"
                "Automatic replacement is only available from the packaged Windows exe.",
            )
            return
        try:
            install_update_after_exit(downloaded_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Update Install Failed", str(exc))
            return
        self.statusBar().showMessage("Installing app update")
        QApplication.quit()

    def _show_update_check_failure(self, traceback_text: str) -> None:
        if self._closing:
            return
        if self._manual_update_check:
            QMessageBox.critical(self, "Update Check Failed", traceback_text)

    def _show_update_download_failure(self, traceback_text: str) -> None:
        if self._closing:
            return
        self._loading_label.setVisible(False)
        self._loading_bar.setVisible(False)
        if "App update download cancelled" in traceback_text:
            self.statusBar().showMessage("App update download cancelled")
            return
        QMessageBox.critical(self, "Update Download Failed", traceback_text)

    def _on_update_check_finished(self) -> None:
        if self._update_check_thread is not None:
            self._update_check_thread.deleteLater()
        self._update_check_thread = None
        self._update_check_worker = None
        if self._closing:
            self.close()

    def _on_update_download_finished(self) -> None:
        if self._update_download_thread is not None:
            self._update_download_thread.deleteLater()
        self._update_download_thread = None
        self._update_download_worker = None
        if self._closing:
            self.close()

    def _check_for_whats_new(self) -> None:
        if self._closing or self._release_notes_thread is not None:
            return
        self._release_notes_thread, self._release_notes_worker = start_release_notes_worker(
            __version__
        )
        self._release_notes_worker.release_notes_ready.connect(self._show_whats_new_dialog)
        self._release_notes_worker.failed.connect(self._on_release_notes_failed)
        self._release_notes_worker.finished.connect(self._on_release_notes_finished)
        self._release_notes_thread.start()

    def _show_whats_new_dialog(self, notes: str) -> None:
        if self._closing:
            return
        release_url = (
            f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tag/v{__version__}"
        )
        dialog = QDialog(self)
        dialog.setWindowTitle(f"What's New in LeagueCastAssist v{__version__}")
        dialog.resize(560, 460)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        header = QLabel(f"Updated to LeagueCastAssist v{__version__}")
        header.setObjectName("SectionTitle")
        layout.addWidget(header)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        if notes:
            browser.setMarkdown(notes)
        else:
            browser.setPlainText(
                "Release notes are not available.\n\n"
                f"View the full changelog at:\n{release_url}"
            )
        layout.addWidget(browser, stretch=1)

        buttons = QDialogButtonBox()
        github_btn = buttons.addButton("View on GitHub", QDialogButtonBox.ButtonRole.ActionRole)
        github_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(release_url)))
        ok_btn = buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        ok_btn.clicked.connect(dialog.accept)
        layout.addWidget(buttons)

        dialog.exec()

    def _on_release_notes_failed(self, _traceback_text: str) -> None:
        if not self._closing:
            self._show_whats_new_dialog("")

    def _on_release_notes_finished(self) -> None:
        if self._release_notes_thread is not None:
            self._release_notes_thread.deleteLater()
        self._release_notes_thread = None
        self._release_notes_worker = None
        if self._closing:
            self.close()

    def _set_auto_updates_enabled(self, enabled: bool) -> None:
        self._settings.updates.auto_check = enabled
        save_settings(self._settings)

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            "About LeagueCastAssist",
            f"LeagueCastAssist {__version__}\n\n"
            "A native desktop companion app for League of Legends custom-game casters.",
        )

    def _refresh_visible_images(self) -> None:
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

    def _show_patch_update_available(self, live_version: str, cached_version: str) -> None:
        QMessageBox.information(
            self,
            "CommunityDragon Update Available",
            (
                "A newer CommunityDragon patch is available.\n\n"
                f"Installed data: {cached_version}\n"
                f"Current patch: {live_version}\n\n"
                "LeagueCastAssist will download the current metadata now. "
                "Use Settings > Download All In-Game Data if you want to pre-cache "
                "every champion's ability data and icons for this patch."
            ),
        )

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self._state, self)
        if not dialog.exec():
            return

        self._settings = dialog.settings
        save_settings(self._settings)
        hover = self._settings.ui.hover_to_describe
        self._blue_panel.set_hover_to_describe(hover)
        self._red_panel.set_hover_to_describe(hover)
        self._blue_panel.update_team(self._state.blue_team)
        self._red_panel.update_team(self._state.red_team)
        self._restart_worker()

    def _open_debug_simulation(self) -> None:
        if self._debug_data_thread is not None:
            return
        self.statusBar().showMessage("Loading debug champion data")
        self._debug_data_thread, self._debug_data_worker = start_debug_data_worker(
            self._settings.model_copy(deep=True)
        )
        self._debug_data_worker.champions_ready.connect(self._show_debug_dialog)
        self._debug_data_worker.failed.connect(self._show_debug_data_failure)
        self._debug_data_worker.finished.connect(self._on_debug_data_finished)
        self._debug_data_thread.start()

    def _show_debug_dialog(self, champions, items) -> None:  # noqa: ANN001
        if self._closing:
            return
        dialog = DebugSimulationDialog(
            champions,
            items,
            self._debug_champion_ids,
            self._debug_item_ids_by_player,
            self,
        )
        if not dialog.exec():
            return

        self._debug_champion_ids = dialog.selected_champion_ids()
        self._debug_item_ids_by_player = dialog.selected_item_ids_by_player()
        self._start_debug_simulation()

    def _show_debug_data_failure(self, traceback_text: str) -> None:
        if self._closing:
            return
        QMessageBox.critical(self, "Debug data unavailable", traceback_text)

    def _on_debug_data_finished(self) -> None:
        if self._debug_data_thread is not None:
            self._debug_data_thread.deleteLater()
        self._debug_data_thread = None
        self._debug_data_worker = None
        if self._closing:
            self.close()

    def _start_debug_simulation(self) -> None:
        if self._debug_simulation_thread is not None:
            return
        self._debug_simulation_active = True
        self._stop_debug_action.setEnabled(True)
        if self._worker is not None:
            self._worker.stop()
        self.statusBar().showMessage("Starting debug simulation")
        (
            self._debug_simulation_thread,
            self._debug_simulation_worker,
        ) = start_debug_simulation_worker(
            self._settings.model_copy(deep=True),
            self._debug_champion_ids,
            self._debug_item_ids_by_player,
        )
        self._debug_simulation_worker.state_ready.connect(self._debug_simulation_ready)
        self._debug_simulation_worker.failed.connect(self._show_debug_simulation_failure)
        self._debug_simulation_worker.finished.connect(self._on_debug_simulation_finished)
        self._debug_simulation_thread.start()

    def _debug_simulation_ready(self, state: MatchState) -> None:
        if self._closing:
            return
        self._update_state(state)

    def _show_debug_simulation_failure(self, traceback_text: str) -> None:
        self._debug_simulation_active = False
        self._stop_debug_action.setEnabled(False)
        if self._closing:
            return
        QMessageBox.critical(self, "Debug simulation failed", traceback_text)

    def _on_debug_simulation_finished(self) -> None:
        if self._debug_simulation_thread is not None:
            self._debug_simulation_thread.deleteLater()
        self._debug_simulation_thread = None
        self._debug_simulation_worker = None
        if self._closing:
            self.close()

    def _stop_debug_simulation(self) -> None:
        self._debug_simulation_active = False
        self._stop_debug_action.setEnabled(False)
        self.statusBar().showMessage("Debug simulation stopped")
        if self._worker is None:
            self._start_worker()
        else:
            self._restart_after_stop = True

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
            QLabel#PlayerMeta {
                color: #9aa4b2;
                font-size: 11px;
            }
            QLabel#AbilityName {
                font-size: 11px;
            }
            QLabel#Muted {
                color: #9aa4b2;
            }
            QLabel#ComparisonAmount {
                color: #f2d27a;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#ComparisonArrowBlue {
                color: #5fa8ff;
                font-size: 8px;
            }
            QLabel#ComparisonArrowRed {
                color: #ff6b6b;
                font-size: 8px;
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
            QFrame#RoleComparisonPanel {
                background: transparent;
            }
            QFrame#RoleComparisonMarker {
                border: 1px solid #30394a;
                border-radius: 9px;
                background: #11161f;
            }
            QFrame#PlayerCard {
                padding: 0px;
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
            QTextBrowser {
                background: #141922;
                border: 1px solid #2d3440;
                border-radius: 8px;
                padding: 4px;
            }
            QSplitter::handle {
                background: #232a35;
            }
            """
        )
