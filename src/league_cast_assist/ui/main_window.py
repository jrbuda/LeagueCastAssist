from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from league_cast_assist import __version__
from league_cast_assist.config import AppSettings, load_settings, music_db_path, save_settings
from league_cast_assist.data.game_activity import ActivityEvent, GameActivityTracker
from league_cast_assist.models import MatchState
from league_cast_assist.music.library import MusicLibrary
from league_cast_assist.music.paths import media_root
from league_cast_assist.music.playback import PlaybackEngine
from league_cast_assist.resources import app_icon_path, seed_music_db_path
from league_cast_assist.ui.debug_dialog import DebugSimulationDialog
from league_cast_assist.ui.detachable_tabs import DetachableTabWidget, clamp_geometry
from league_cast_assist.ui.match_page import MatchPage
from league_cast_assist.ui.media_download_dialog import MediaDownloadDialog
from league_cast_assist.ui.music_page import MusicPage
from league_cast_assist.ui.music_strip import MusicStrip
from league_cast_assist.ui.now_playing_window import NowPlayingWindow
from league_cast_assist.ui.onboarding import FirstLaunchDialog
from league_cast_assist.ui.settings_dialog import SettingsDialog
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

ACTIVITY_TICK_MS = 1000


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
        self._initial_focus_done = False
        self._auto_start_remaining = 0
        self._overlay: NowPlayingWindow | None = None

        self.setWindowTitle("LeagueCastAssist")
        icon_path = app_icon_path()
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1280, 860)

        # Music stack: engine + library outlive the page so tear-off is lossless.
        self._media_root = media_root(self._settings)
        self._library = MusicLibrary(music_db_path(), seed_music_db_path())
        self._library.open()
        self._playback = PlaybackEngine(self)

        _was_first_launch_complete = self._settings.first_launch_complete
        if show_onboarding:
            self._show_first_launch_if_needed()
        self._build_ui()
        self._build_menu()
        self._build_shortcuts()
        self.menuBar().hide()
        self._sync_overlay()
        self._sync_strip()

        self._activity = GameActivityTracker(
            idle_return_delay_seconds=self._settings.focus.idle_return_delay_seconds
        )
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(ACTIVITY_TICK_MS)
        self._activity_timer.timeout.connect(self._tick_activity)
        self._activity_timer.start()

        self._auto_start_timer = QTimer(self)
        self._auto_start_timer.setInterval(1000)
        self._auto_start_timer.timeout.connect(self._auto_start_tick)

        # Land on the page the user will want while no game state is known yet;
        # a detected game flips to the match page on the first poll.
        if self._settings.focus.show_music_when_idle and not self._tabs.is_detached(
            self._music_page
        ):
            self._tabs.setCurrentWidget(self._music_page)
        self._initial_focus_done = True
        self._sync_strip()

        if start_worker:
            self._start_worker()
        if self._settings.updates.auto_check and is_frozen_app():
            QTimer.singleShot(2500, self._check_for_updates_auto)
        if show_onboarding and self._settings.last_seen_version != __version__:
            self._settings.last_seen_version = __version__
            save_settings(self._settings)
            if _was_first_launch_complete:
                QTimer.singleShot(800, self._check_for_whats_new)

    # ── construction ───────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 4, 8, 6)
        root_layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)
        self._file_button = QPushButton("File")
        header.addWidget(self._file_button)

        title = QLabel("LeagueCastAssist")
        title.setObjectName("Header")
        header.addWidget(title)
        header.addSpacing(12)

        self._match_page = MatchPage(self._settings)
        self._music_page = MusicPage(
            self._settings,
            self._library,
            self._playback,
            self._media_root,
        )
        self._tabs = DetachableTabWidget()
        self._tabs.addTab(self._match_page, "Match")
        self._tabs.addTab(self._music_page, "Music")
        self._tabs.setCurrentIndex(0)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._tabs.page_detached.connect(self._on_page_detached)
        self._tabs.page_redocked.connect(self._on_page_redocked)

        self._status_pill = QLabel("Starting")
        self._status_pill.setObjectName("StatusPillIdle")
        header.addWidget(self._status_pill)
        header.addStretch(1)

        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.setToolTip("Poll Riot local APIs immediately")
        self._refresh_button.clicked.connect(self._refresh_now)
        header.addWidget(self._refresh_button)

        settings = QPushButton("Settings")
        settings.setToolTip("Assets, focus behaviour, music and media server")
        settings.clicked.connect(self.open_settings)
        header.addWidget(settings)
        root_layout.addLayout(header)
        root_layout.addWidget(self._tabs, stretch=1)

        self._strip = MusicStrip(self._media_root)
        self._strip.pause_requested.connect(self._music_page.toggle_pause)
        self._strip.next_requested.connect(self._music_page.next_weighted)
        self._strip.end_break_requested.connect(self._music_page.end_break)
        self._strip.open_page_requested.connect(lambda: self._focus_music(raise_window=True))
        self._strip.setVisible(False)
        root_layout.addWidget(self._strip)

        self.setCentralWidget(root)

        status = QStatusBar()
        status.showMessage("Starting")
        self.setStatusBar(status)

        self._match_page.status_message.connect(self.statusBar().showMessage)
        self._music_page.status_message.connect(self.statusBar().showMessage)
        self._music_page.settings_changed.connect(self._persist_settings)
        self._music_page.request_media_download.connect(self.open_media_download)
        self._music_page.track_changed.connect(self._on_track_changed)
        self._playback.position_changed.connect(self._on_music_position)
        self._playback.state_changed.connect(self._on_music_state)

        # Restore a previously detached music page.
        if self._settings.music.detached:
            geometry = clamp_geometry(self._settings.music.detached_geometry)
            QTimer.singleShot(0, lambda: self._restore_detached_music(geometry))

    def _restore_detached_music(self, geometry: tuple[int, int, int, int] | None) -> None:
        if self._tabs.detach_page(self._music_page, geometry):
            self._remember_detached(True)
            self._sync_strip()

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+1"), self, activated=self._show_match_tab)
        QShortcut(QKeySequence("Ctrl+2"), self, activated=self._show_music_tab)
        QShortcut(QKeySequence("Ctrl+Space"), self, activated=self._music_page.toggle_pause)
        QShortcut(QKeySequence("Ctrl+Shift+D"), self, activated=self._dock_music)

    def _build_menu(self) -> None:
        file_menu = QMenu(self)
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(settings_action)

        update_action = QAction("Check for Updates", self)
        update_action.triggered.connect(self._check_for_updates_manual)
        file_menu.addAction(update_action)

        self._auto_update_action = QAction("Check for Updates Automatically", self)
        self._auto_update_action.setCheckable(True)
        self._auto_update_action.setChecked(self._settings.updates.auto_check)
        self._auto_update_action.toggled.connect(self._set_auto_updates_enabled)
        file_menu.addAction(self._auto_update_action)

        file_menu.addSeparator()
        dock_action = QAction("Dock Music Page", self)
        dock_action.triggered.connect(self._dock_music)
        file_menu.addAction(dock_action)

        overlay_action = QAction("Show OBS Now Playing Window", self)
        overlay_action.setCheckable(True)
        overlay_action.setChecked(self._settings.music.overlay_enabled)
        overlay_action.toggled.connect(self._set_overlay_enabled)
        self._overlay_action = overlay_action
        file_menu.addAction(overlay_action)

        media_action = QAction("Music Media…", self)
        media_action.triggered.connect(self.open_media_download)
        file_menu.addAction(media_action)

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
            self._match_page.set_debug_objectives_visible
        )
        debug_menu.addAction(self._show_objectives_graph_action)
        self._file_button.setMenu(file_menu)

    # ── first launch ───────────────────────────────────────────────────────
    def _show_first_launch_if_needed(self) -> None:
        if self._settings.first_launch_complete:
            return

        dialog = FirstLaunchDialog(self._settings, self)
        if dialog.exec():
            self._settings = dialog.settings
            save_settings(self._settings)

    # ── workers ────────────────────────────────────────────────────────────
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

    def _show_worker_failure(self, traceback_text: str) -> None:
        QMessageBox.critical(self, "Data worker failed", traceback_text)

    def _on_worker_finished(self) -> None:
        self._match_page.refresh_images()

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

    # ── state ──────────────────────────────────────────────────────────────
    def _update_state(self, state: MatchState) -> None:
        if self._debug_simulation_active and state.source != "debug":
            return
        self._state = state
        self._match_page.update_state(state)
        self.statusBar().showMessage(state.status)

        if self._debug_simulation_active or state.source == "debug":
            self._update_status_pill(state)
            return

        # Resolve the activity transition first so the pill and the page
        # switch agree with each other.
        event = self._activity.update(state)
        self._update_status_pill(state)

        if not event and not self._initial_focus_done:
            self._initial_focus_done = True
            if not self._activity.in_game and not state.loading_active:
                self._focus_music(raise_window=self._settings.focus.raise_music_when_idle)
            else:
                self._focus_match(raise_window=False)
            return
        if event is not None:
            self._handle_activity(event)

    def _tick_activity(self) -> None:
        if self._debug_simulation_active:
            return
        event = self._activity.tick()
        if event is not None:
            self._handle_activity(event)

    def _handle_activity(self, event: ActivityEvent) -> None:
        self._initial_focus_done = True
        if event == ActivityEvent.GAME_STARTED:
            self._cancel_auto_start()
            self._focus_match(raise_window=self._settings.focus.raise_match_in_game)
        elif event == ActivityEvent.GAME_ENDED:
            self._focus_music(raise_window=self._settings.focus.raise_music_when_idle)
            self._maybe_schedule_auto_start()
        self._update_status_pill(self._state)

    def _focus_music(self, raise_window: bool) -> None:
        if self._settings.focus.show_music_when_idle and not self._tabs.is_detached(
            self._music_page
        ):
            self._tabs.setCurrentWidget(self._music_page)
        window = self._tabs.detached_window(self._music_page)
        if window is not None:
            window.show()
            if raise_window:
                self._raise_window(window)
        elif raise_window:
            self._raise_window(self)
        self._sync_strip()

    def _focus_match(self, raise_window: bool) -> None:
        if self._settings.focus.show_match_in_game:
            if self._tabs.is_detached(self._match_page):
                self._tabs.redock(self._match_page)
            self._tabs.setCurrentWidget(self._match_page)
        if raise_window:
            self._raise_window(self)
        self._sync_strip()

    @staticmethod
    def _raise_window(window: QWidget) -> None:
        if window.isMinimized():
            window.showNormal()
        window.raise_()
        window.activateWindow()

    def _update_status_pill(self, state: MatchState) -> None:
        if self._debug_simulation_active or state.source == "debug":
            self._set_pill("● Debug simulation", "StatusPillMusic")
            return
        if self._activity.in_game:
            if state.game_time_seconds:
                minutes, seconds = divmod(int(state.game_time_seconds), 60)
                self._set_pill(f"● In game — {minutes}:{seconds:02d}", "StatusPillLive")
            else:
                self._set_pill("● In game", "StatusPillLive")
            return
        if state.source == "none" and not self._activity.seen_any_state:
            self._set_pill("● League client not connected", "StatusPillIdle")
            return
        if state.source == "none":
            self._set_pill("● League client not connected", "StatusPillIdle")
            return
        phase = state.phase or "Connected"
        self._set_pill(f"● {phase} — music focus", "StatusPillMusic")

    def _set_pill(self, text: str, object_name: str) -> None:
        self._status_pill.setText(text)
        if self._status_pill.objectName() != object_name:
            self._status_pill.setObjectName(object_name)
            self._status_pill.style().unpolish(self._status_pill)
            self._status_pill.style().polish(self._status_pill)

    # ── auto start ─────────────────────────────────────────────────────────
    def _maybe_schedule_auto_start(self) -> None:
        if not self._settings.music.auto_start_on_game_end:
            return
        delay = int(self._settings.music.auto_start_delay_seconds)
        if delay <= 0:
            self._start_music_now()
            return
        self._auto_start_remaining = delay
        self.statusBar().showMessage(f"Music starts in {delay}s")
        self._auto_start_timer.start()

    def _auto_start_tick(self) -> None:
        self._auto_start_remaining -= 1
        if self._auto_start_remaining <= 0:
            self._auto_start_timer.stop()
            self._start_music_now()
            return
        self.statusBar().showMessage(f"Music starts in {self._auto_start_remaining}s")

    def _cancel_auto_start(self) -> None:
        if self._auto_start_timer.isActive():
            self._auto_start_timer.stop()
            self.statusBar().showMessage("Automatic music start cancelled")

    def _start_music_now(self) -> None:
        self.statusBar().showMessage("Starting music automatically")
        self._music_page.start_break()

    # ── music wiring ───────────────────────────────────────────────────────
    def _on_track_changed(self, song) -> None:  # noqa: ANN001
        self._strip.set_song(song)
        if self._overlay is not None:
            if song is None:
                self._overlay.clear_song()
            else:
                self._overlay.show_song(song)
        self._sync_strip()

    def _on_music_position(self, position: int, duration: int) -> None:
        self._strip.set_position(position, duration)
        if self._overlay is not None:
            self._overlay.update_progress(position, duration)

    def _on_music_state(self, state: str) -> None:
        self._strip.set_playback_state(state)
        self._sync_strip()

    def _sync_strip(self) -> None:
        enabled = self._settings.music.in_match_strip
        active = (
            self._playback.state in {"playing", "paused", "fading"}
            and self._music_page.current_song is not None
        )
        music_visible = (
            self._tabs.currentWidget() is self._music_page
            and not self._tabs.is_detached(self._music_page)
        )
        self._strip.setVisible(bool(enabled and active and not music_visible))

    def _sync_overlay(self) -> None:
        wanted = self._settings.music.overlay_enabled
        if wanted and self._overlay is None:
            self._overlay = NowPlayingWindow(self._media_root)
            current = self._music_page.current_song
            if current is not None:
                self._overlay.show_song(current)
            self._overlay.show()
        elif not wanted and self._overlay is not None:
            self._overlay.close()
            self._overlay.deleteLater()
            self._overlay = None

    def _set_overlay_enabled(self, enabled: bool) -> None:
        self._settings.music.overlay_enabled = bool(enabled)
        self._sync_overlay()
        self._persist_settings()

    def _on_tab_changed(self, _index: int) -> None:
        on_match = self._tabs.currentWidget() is self._match_page
        self._refresh_button.setEnabled(on_match)
        self._sync_strip()

    def _on_page_detached(self, page) -> None:  # noqa: ANN001
        self._remember_detached(page is self._music_page)
        if page is self._music_page and self._settings.music.overlay_enabled:
            self._sync_overlay()
        self._sync_strip()

    def _on_page_redocked(self, page) -> None:  # noqa: ANN001
        if page is self._music_page:
            self._remember_detached(False)
        self._sync_strip()

    def _remember_detached(self, detached: bool) -> None:
        self._settings.music.detached = bool(detached)
        window = self._tabs.detached_window(self._music_page)
        if detached and window is not None:
            geometry = window.geometry()
            self._settings.music.detached_geometry = [
                geometry.x(),
                geometry.y(),
                geometry.width(),
                geometry.height(),
            ]
        elif not detached:
            self._settings.music.detached_geometry = None
        self._persist_settings()

    def _dock_music(self) -> None:
        if self._tabs.is_detached(self._music_page):
            self._tabs.redock(self._music_page)
            self.statusBar().showMessage("Music page docked")

    def _show_match_tab(self) -> None:
        if self._tabs.is_detached(self._match_page):
            self._tabs.redock(self._match_page)
        self._tabs.setCurrentWidget(self._match_page)

    def _show_music_tab(self) -> None:
        window = self._tabs.detached_window(self._music_page)
        if window is not None:
            self._raise_window(window)
            return
        self._tabs.setCurrentWidget(self._music_page)

    # ── settings ───────────────────────────────────────────────────────────
    def open_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self._state, self)
        if not dialog.exec():
            return

        self._settings = dialog.settings
        save_settings(self._settings)
        self._match_page.set_hover_to_describe(self._settings.ui.hover_to_describe)
        self._apply_media_root()
        self._music_page.apply_settings()
        self._activity.set_idle_delay(self._settings.focus.idle_return_delay_seconds)
        self._overlay_action.blockSignals(True)
        self._overlay_action.setChecked(self._settings.music.overlay_enabled)
        self._overlay_action.blockSignals(False)
        self._sync_overlay()
        self._music_page.refresh_all()
        self._sync_strip()
        self._restart_worker()

    def open_media_download(self) -> None:
        dialog = MediaDownloadDialog(self._settings, music_db_path(), self)
        dialog.exec()
        self._settings.catalog.base_url = dialog.settings.catalog.base_url
        self._settings.catalog.manifest = dialog.settings.catalog.manifest
        save_settings(self._settings)
        if dialog.synced:
            self._library.reload()
            self._music_page.refresh_all()
            self.statusBar().showMessage("Music library updated")

    def _apply_media_root(self) -> None:
        root = media_root(self._settings)
        if root == self._media_root:
            return
        self._media_root = root
        self._music_page.set_media_root(root)
        self._strip.set_media_root(root)
        if self._overlay is not None:
            self._overlay.close()
            self._overlay.deleteLater()
            self._overlay = None
            self._sync_overlay()

    def _persist_settings(self) -> None:
        save_settings(self._settings)

    # ── updates (unchanged behaviour) ──────────────────────────────────────
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
        self._update_download_thread, self._update_download_worker = start_update_download_worker(
            release
        )
        self._update_download_worker.progress_updated.connect(self._on_app_download_progress)
        self._update_download_worker.update_downloaded.connect(self._install_downloaded_update)
        self._update_download_worker.failed.connect(self._show_update_download_failure)
        self._update_download_worker.finished.connect(self._on_update_download_finished)
        self._update_download_thread.start()

    def _on_app_download_progress(self, message: str, current: int, total: int) -> None:
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
        release_url = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tag/v{__version__}"
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

    # ── debug simulation ───────────────────────────────────────────────────
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
        self._show_match_tab()
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

    # ── shutdown ───────────────────────────────────────────────────────────
    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._closing:
            if self._has_running_thread():
                event.ignore()
                return
            self._shutdown_music()
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
        self._shutdown_music()
        super().closeEvent(event)

    def _shutdown_music(self) -> None:
        self._auto_start_timer.stop()
        self._activity_timer.stop()
        self._tabs.redock_all()
        if self._overlay is not None:
            self._overlay.close()
            self._overlay.deleteLater()
            self._overlay = None
        self._playback.stop()
        self._library.close()

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
