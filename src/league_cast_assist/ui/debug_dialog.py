from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
)

from league_cast_assist.data.static_data import ChampionSummaryData


class DebugSimulationDialog(QDialog):
    def __init__(
        self,
        champions: list[ChampionSummaryData],
        current_ids: list[int] | None = None,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Debug Champion Simulation")
        self._champions = sorted(champions, key=lambda champion: champion.name.lower())
        self._selectors: list[QComboBox] = []

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Select up to 10 champions to simulate a match state. "
            "This pauses live polling until debug simulation is stopped."
        )
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        layout.addWidget(intro)

        grid = QGridLayout()
        defaults = current_ids or [champion.champion_id for champion in self._champions[:10]]
        for index in range(10):
            side = "Blue" if index < 5 else "Red"
            label = QLabel(f"{side} {index % 5 + 1}")
            selector = QComboBox()
            selector.setMinimumWidth(220)
            for champion in self._champions:
                selector.addItem(champion.name, champion.champion_id)
            if index < len(defaults):
                selected_index = selector.findData(defaults[index])
                if selected_index >= 0:
                    selector.setCurrentIndex(selected_index)
            self._selectors.append(selector)
            grid.addWidget(label, index, 0)
            grid.addWidget(selector, index, 1)
        layout.addLayout(grid)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, alignment=Qt.AlignmentFlag.AlignRight)

    def selected_champion_ids(self) -> list[int]:
        return [int(selector.currentData()) for selector in self._selectors]
