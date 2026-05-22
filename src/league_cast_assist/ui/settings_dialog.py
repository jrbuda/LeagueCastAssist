from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
)

from league_cast_assist.config import AppSettings
from league_cast_assist.models import MatchState


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

    def _accept(self) -> None:
        self.settings.assets.mode = "local" if self._local_assets.isChecked() else "remote"

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
        self.accept()
