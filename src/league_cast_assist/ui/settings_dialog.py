from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from league_cast_assist.config import AppSettings, music_db_path
from league_cast_assist.models import MatchState
from league_cast_assist.music.catalog import MANIFEST_HINT
from league_cast_assist.music.paths import media_root
from league_cast_assist.ui.media_download_dialog import MediaDownloadDialog
from league_cast_assist.ui.workers import (
    StaticDataDownloadWorker,
    start_static_data_download_worker,
)


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, state: MatchState, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.settings = settings.model_copy(deep=True)
        self._media_changed = False
        self.setWindowTitle("LeagueCastAssist Settings")
        self.resize(720, 640)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("SettingsTabs")
        self._tabs.addTab(self._build_assets_tab(), "Assets")
        self._tabs.addTab(self._build_interface_tab(state), "Interface")
        self._tabs.addTab(self._build_music_tab(), "Music")
        self._tabs.addTab(self._build_focus_tab(), "Focus")
        self._tabs.addTab(self._build_catalog_tab(), "Media Server")
        layout.addWidget(self._tabs, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── Assets ─────────────────────────────────────────────────────────────
    def _build_assets_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Asset Loading"))
        self._local_assets = QRadioButton("Download assets locally")
        self._remote_assets = QRadioButton("Use internet assets directly")
        self._local_assets.setChecked(self.settings.assets.mode == "local")
        self._remote_assets.setChecked(self.settings.assets.mode == "remote")
        layout.addWidget(self._local_assets)
        layout.addWidget(self._remote_assets)

        download_row = QHBoxLayout()
        self._download_all_button = QPushButton("Download All In-Game Data")
        self._download_all_button.setToolTip(
            "Download CDragon data and icons needed to render any champion in a later game."
        )
        self._download_all_button.clicked.connect(self._download_all_in_game_data)
        download_row.addWidget(self._download_all_button)
        self._download_status = QLabel("")
        self._download_status.setObjectName("Muted")
        download_row.addWidget(self._download_status, stretch=1)
        layout.addLayout(download_row)

        self._download_progress = QProgressBar()
        self._download_progress.setVisible(False)
        layout.addWidget(self._download_progress)

        self._download_thread = None
        self._download_worker: StaticDataDownloadWorker | None = None
        layout.addStretch(1)
        return page

    # ── Interface ──────────────────────────────────────────────────────────
    def _build_interface_tab(self, state: MatchState) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)

        self._hover_to_describe = QCheckBox("Show ability/item details on hover")
        self._hover_to_describe.setChecked(self.settings.ui.hover_to_describe)
        layout.addWidget(self._hover_to_describe)

        form = QFormLayout()
        self._blue_team = QLineEdit(self.settings.team_name_overrides.get("blue", ""))
        self._blue_team.setPlaceholderText(state.blue_team.display_name)
        self._red_team = QLineEdit(self.settings.team_name_overrides.get("red", ""))
        self._red_team.setPlaceholderText(state.red_team.display_name)
        form.addRow("Blue team override", self._blue_team)
        form.addRow("Red team override", self._red_team)
        layout.addLayout(form)

        players_box = QGroupBox("Player name overrides")
        players_layout = QVBoxLayout(players_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QFormLayout(inner)
        self._player_edits: dict[str, QLineEdit] = {}
        for player in state.players:
            edit = QLineEdit(self.settings.player_name_overrides.get(player.stable_key, ""))
            edit.setPlaceholderText(player.display_name)
            self._player_edits[player.stable_key] = edit
            inner_layout.addRow(player.display_name, edit)
        if not state.players:
            empty = QLabel("Player overrides appear once a game is detected.")
            empty.setObjectName("Muted")
            inner_layout.addRow(empty)
        scroll.setWidget(inner)
        players_layout.addWidget(scroll)
        layout.addWidget(players_box, 1)

        hint = QLabel("Leave override fields blank to use League-provided names.")
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        return page

    # ── Music ──────────────────────────────────────────────────────────────
    def _build_music_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)

        playback_box = QGroupBox("Playback")
        playback = QFormLayout(playback_box)
        self._volume = QSpinBox()
        self._volume.setRange(0, 100)
        self._volume.setValue(self.settings.music.volume)
        self._volume.setSuffix(" %")
        playback.addRow("Volume", self._volume)
        self._overlay = QCheckBox("Show the 500×200 OBS \u201cNow Playing\u201d window")
        self._overlay.setChecked(self.settings.music.overlay_enabled)
        playback.addRow("", self._overlay)
        self._strip = QCheckBox("Show the music strip on the Match page")
        self._strip.setChecked(self.settings.music.in_match_strip)
        playback.addRow("", self._strip)

        self._stream_only = QCheckBox("Send music to the stream only (I won't hear it)")
        self._stream_only.setToolTip(
            "Routes music to a virtual audio device that OBS captures and keeps it "
            "out of your speakers."
        )
        self._stream_only.setChecked(self.settings.music.stream_only)
        playback.addRow("", self._stream_only)
        stream_note = QLabel(
            "Stream-only needs a virtual audio device: VB-Cable, Voicemod or "
            "NVIDIA Broadcast. The app picks one automatically when it finds it; "
            "in OBS use Audio Output Capture on the matching device."
        )
        stream_note.setObjectName("Muted")
        stream_note.setWordWrap(True)
        playback.addRow("", stream_note)

        note = QLabel("Audio output device and producer monitoring live on the Music page.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        playback.addRow("", note)
        layout.addWidget(playback_box)

        folder_box = QGroupBox("Media folder")
        folder = QVBoxLayout(folder_box)
        folder_row = QHBoxLayout()
        self._media_dir = QLineEdit(self.settings.music.media_dir)
        self._media_dir.setPlaceholderText(str(media_root(self.settings)))
        folder_row.addWidget(self._media_dir, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_media_dir)
        folder_row.addWidget(browse)
        open_button = QPushButton("Open")
        open_button.clicked.connect(self._open_media_dir)
        folder_row.addWidget(open_button)
        folder.addLayout(folder_row)
        folder_hint = QLabel(
            "Leave blank to use assets/media beside the app. Tracks live in "
            "music/ and artwork in artwork/ inside that folder."
        )
        folder_hint.setObjectName("Muted")
        folder_hint.setWordWrap(True)
        folder.addWidget(folder_hint)
        layout.addWidget(folder_box)
        layout.addStretch(1)
        return page

    # ── Focus ──────────────────────────────────────────────────────────────
    def _build_focus_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)

        idle_box = QGroupBox("When no game is running")
        idle = QVBoxLayout(idle_box)
        self._show_music_when_idle = QCheckBox("Show the Music page")
        self._show_music_when_idle.setChecked(self.settings.focus.show_music_when_idle)
        idle.addWidget(self._show_music_when_idle)
        self._raise_music_when_idle = QCheckBox("Bring the window to the front")
        self._raise_music_when_idle.setChecked(self.settings.focus.raise_music_when_idle)
        idle.addWidget(self._raise_music_when_idle)
        idle_form = QFormLayout()
        self._idle_delay = QSpinBox()
        self._idle_delay.setRange(0, 120)
        self._idle_delay.setValue(int(self.settings.focus.idle_return_delay_seconds))
        self._idle_delay.setSuffix(" s")
        self._idle_delay.setToolTip(
            "Grace period so loading screens and reconnects do not switch pages."
        )
        idle_form.addRow("Return to music after", self._idle_delay)
        idle.addLayout(idle_form)
        layout.addWidget(idle_box)

        game_box = QGroupBox("When a game is detected")
        game = QVBoxLayout(game_box)
        self._show_match_in_game = QCheckBox("Show the Match page")
        self._show_match_in_game.setChecked(self.settings.focus.show_match_in_game)
        game.addWidget(self._show_match_in_game)
        self._raise_match_in_game = QCheckBox("Bring the window to the front")
        self._raise_match_in_game.setChecked(self.settings.focus.raise_match_in_game)
        game.addWidget(self._raise_match_in_game)
        layout.addWidget(game_box)

        auto_box = QGroupBox("Automatic playback")
        auto = QVBoxLayout(auto_box)
        auto_row = QHBoxLayout()
        self._auto_start = QCheckBox("Start music automatically when a game ends, after")
        self._auto_start.setChecked(self.settings.music.auto_start_on_game_end)
        auto_row.addWidget(self._auto_start)
        self._auto_start_delay = QSpinBox()
        self._auto_start_delay.setRange(0, 600)
        self._auto_start_delay.setValue(int(self.settings.music.auto_start_delay_seconds))
        self._auto_start_delay.setSuffix(" s")
        auto_row.addWidget(self._auto_start_delay)
        auto_row.addStretch(1)
        auto.addLayout(auto_row)
        auto_hint = QLabel(
            "Music starts with the same weighted pick as Start Break, and is cancelled "
            "if a new game starts first."
        )
        auto_hint.setObjectName("Muted")
        auto_hint.setWordWrap(True)
        auto.addWidget(auto_hint)
        layout.addWidget(auto_box)
        layout.addStretch(1)
        return page

    # ── Media server ───────────────────────────────────────────────────────
    def _build_catalog_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)

        form = QFormLayout()
        self._catalog_url = QLineEdit(self.settings.catalog.base_url)
        self._catalog_url.setPlaceholderText("https://media.example.com/music")
        form.addRow("Media server URL", self._catalog_url)
        self._catalog_manifest = QLineEdit(self.settings.catalog.manifest)
        self._catalog_manifest.setPlaceholderText("manifest.json")
        form.addRow("Manifest file", self._catalog_manifest)
        layout.addLayout(form)

        hint = QLabel(MANIFEST_HINT)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        self._media_button = QPushButton("Download / verify media…")
        self._media_button.clicked.connect(self._open_media_dialog)
        row.addWidget(self._media_button)
        open_folder = QPushButton("Open media folder")
        open_folder.clicked.connect(self._open_media_dir)
        row.addWidget(open_folder)
        row.addStretch(1)
        layout.addLayout(row)

        self._media_status = QLabel(f"Local folder: {media_root(self.settings)}")
        self._media_status.setObjectName("Muted")
        self._media_status.setWordWrap(True)
        layout.addWidget(self._media_status)
        layout.addStretch(1)
        return page

    # ── actions ────────────────────────────────────────────────────────────
    def _browse_media_dir(self) -> None:
        start = self._media_dir.text().strip() or str(media_root(self.settings))
        chosen = QFileDialog.getExistingDirectory(self, "Choose the media folder", start)
        if chosen:
            self._media_dir.setText(chosen)
            self._media_changed = True

    def _open_media_dir(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        folder = Path(self._media_dir.text().strip() or media_root(self.settings))
        if not folder.is_dir():
            QMessageBox.information(
                self, "Media folder", f"That folder does not exist yet:\n{folder}"
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _open_media_dialog(self) -> None:
        self._accept_settings_without_closing()
        dialog = MediaDownloadDialog(self.settings, music_db_path(), self)
        if dialog.exec():
            self.settings = dialog.settings
            self._catalog_url.setText(self.settings.catalog.base_url)
            self._catalog_manifest.setText(self.settings.catalog.manifest)
        self.settings.catalog.base_url = self._catalog_url.text().strip()
        self.settings.catalog.manifest = (
            self._catalog_manifest.text().strip() or "manifest.json"
        )

    # ── CommunityDragon download (unchanged behaviour) ──────────────────────
    def reject(self) -> None:
        if self._download_in_progress():
            self._cancel_download()
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._download_in_progress():
            self._cancel_download()
            event.ignore()
            return
        super().closeEvent(event)

    def _cancel_download(self) -> None:
        if self._download_worker is not None:
            self._download_status.setText("Cancelling CommunityDragon download")
            self._download_worker.cancel()

    def _download_all_in_game_data(self) -> None:
        if self._download_in_progress():
            return

        self._accept_settings_without_closing()
        self.settings.assets.mode = "local"
        self._local_assets.setChecked(True)
        self._remote_assets.setChecked(False)
        self._download_all_button.setEnabled(False)
        self._download_status.setText("Starting CommunityDragon download")
        self._download_progress.setVisible(True)
        self._download_progress.setRange(0, 0)
        self._download_progress.setValue(0)

        self._download_thread, self._download_worker = start_static_data_download_worker(
            self.settings.model_copy(deep=True)
        )
        self._download_worker.progress_updated.connect(self._update_download_progress)
        self._download_worker.status_updated.connect(self._download_status.setText)
        self._download_worker.failed.connect(self._show_download_failure)
        self._download_worker.finished.connect(self._download_finished)
        self._download_thread.finished.connect(self._download_thread_finished)
        self._download_thread.start()

    def _update_download_progress(self, message: str, current: int, total: int) -> None:
        if not message and total <= 0:
            self._download_progress.setVisible(False)
            return
        self._download_status.setText(message)
        self._download_progress.setVisible(True)
        if total > 0:
            self._download_progress.setRange(0, total)
            self._download_progress.setValue(current)
        else:
            self._download_progress.setRange(0, 0)

    def _show_download_failure(self, traceback_text: str) -> None:
        if "Static data operation cancelled" in traceback_text:
            self._download_status.setText("CommunityDragon download cancelled")
            return
        self._download_status.setText("CommunityDragon download failed")
        QMessageBox.critical(self, "CommunityDragon Download Failed", traceback_text)

    def _download_finished(self) -> None:
        self._download_all_button.setEnabled(True)
        if self._download_status.text() not in {
            "CommunityDragon download failed",
            "CommunityDragon download cancelled",
        }:
            self._download_status.setText("All in-game CommunityDragon data downloaded")

    def _download_thread_finished(self) -> None:
        if self._download_thread is not None:
            self._download_thread.deleteLater()
        self._download_thread = None
        self._download_worker = None

    def _download_in_progress(self) -> bool:
        return self._download_thread is not None and self._download_thread.isRunning()

    # ── accept ─────────────────────────────────────────────────────────────
    def _accept(self) -> None:
        if self._download_in_progress():
            self._cancel_download()
            return
        self._accept_settings_without_closing()
        self.accept()

    def _accept_settings_without_closing(self) -> None:
        self.settings.assets.mode = "local" if self._local_assets.isChecked() else "remote"
        self.settings.ui.hover_to_describe = self._hover_to_describe.isChecked()

        self.settings.team_name_overrides = {}
        if self._blue_team.text().strip():
            self.settings.team_name_overrides["blue"] = self._blue_team.text().strip()
        if self._red_team.text().strip():
            self.settings.team_name_overrides["red"] = self._red_team.text().strip()

        self.settings.player_name_overrides = {
            key: edit.text().strip()
            for key, edit in self._player_edits.items()
            if edit.text().strip()
        }

        music = self.settings.music
        music.volume = int(self._volume.value())
        music.overlay_enabled = self._overlay.isChecked()
        music.in_match_strip = self._strip.isChecked()
        music.stream_only = self._stream_only.isChecked()
        if music.stream_only:
            music.monitor_enabled = False
        new_media_dir = self._media_dir.text().strip()
        if new_media_dir != music.media_dir:
            self._media_changed = True
        music.media_dir = new_media_dir
        music.auto_start_on_game_end = self._auto_start.isChecked()
        music.auto_start_delay_seconds = float(self._auto_start_delay.value())

        focus = self.settings.focus
        focus.show_music_when_idle = self._show_music_when_idle.isChecked()
        focus.raise_music_when_idle = self._raise_music_when_idle.isChecked()
        focus.show_match_in_game = self._show_match_in_game.isChecked()
        focus.raise_match_in_game = self._raise_match_in_game.isChecked()
        focus.idle_return_delay_seconds = float(self._idle_delay.value())

        self.settings.catalog.base_url = self._catalog_url.text().strip()
        self.settings.catalog.manifest = (
            self._catalog_manifest.text().strip() or "manifest.json"
        )
        self.settings.first_launch_complete = True

    @property
    def media_changed(self) -> bool:
        return self._media_changed

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.key() == Qt.Key.Key_Escape and self._download_in_progress():
            self._cancel_download()
            return
        super().keyPressEvent(event)
