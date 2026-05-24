from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from league_cast_assist.config import AppSettings
from league_cast_assist.models import MatchState
from league_cast_assist.ui.workers import (
    StaticDataDownloadWorker,
    start_static_data_download_worker,
)


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, state: MatchState, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.settings = settings.model_copy(deep=True)
        self.setWindowTitle("LeagueCastAssist Settings")

        layout = QVBoxLayout(self)

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

        layout.addWidget(QLabel("UI Behaviour"))
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

        self._player_edits: dict[str, QLineEdit] = {}
        for player in state.players:
            edit = QLineEdit(self.settings.player_name_overrides.get(player.stable_key, ""))
            edit.setPlaceholderText(player.display_name)
            self._player_edits[player.stable_key] = edit
            form.addRow(f"Player: {player.display_name}", edit)

        layout.addLayout(form)

        hint = QLabel("Leave override fields blank to use League-provided names.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def reject(self) -> None:
        if self._download_in_progress():
            self._cancel_download()
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: ANN001
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
        self.settings.first_launch_complete = True
