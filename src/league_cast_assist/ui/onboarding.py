from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)

from league_cast_assist.config import AppSettings


class FirstLaunchDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.settings = settings.model_copy(deep=True)

        self.setWindowTitle("LeagueCastAssist Setup")

        layout = QVBoxLayout(self)
        prompt = QLabel(
            "Choose how LeagueCastAssist should load game assets. Local caching is recommended "
            "for smoother use during streams."
        )
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        self._local = QRadioButton("Download assets locally (recommended)")
        self._local.setChecked(self.settings.assets.mode == "local")
        layout.addWidget(self._local)

        self._remote = QRadioButton("Use internet assets directly")
        self._remote.setChecked(self.settings.assets.mode == "remote")
        layout.addWidget(self._remote)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self._accept)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        self.settings.assets.mode = "local" if self._local.isChecked() else "remote"
        self.settings.first_launch_complete = True
        self.accept()
