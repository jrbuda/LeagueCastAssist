from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from league_cast_assist.data.static_data import ChampionSummaryData, ItemData


class DebugSimulationDialog(QDialog):
    def __init__(
        self,
        champions: list[ChampionSummaryData],
        items: list[ItemData],
        current_ids: list[int] | None = None,
        current_item_ids: list[list[int]] | None = None,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Debug Champion Simulation")
        self._champions = sorted(champions, key=lambda champion: champion.name.lower())
        self._items = sorted(items, key=lambda item: item.name.lower())
        self._selectors: list[QComboBox] = []
        self._item_selectors: list[list[QComboBox]] = []

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Select up to 10 champions to simulate a match state. "
            "This pauses live polling until debug simulation is stopped."
        )
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        layout.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(1200)
        scroll.setMinimumHeight(520)
        content = QWidget()
        grid = QGridLayout(content)
        defaults = current_ids or [champion.champion_id for champion in self._champions[:10]]
        item_defaults = current_item_ids or []
        grid.addWidget(QLabel("Slot"), 0, 0)
        grid.addWidget(QLabel("Champion"), 0, 1)
        for item_slot in range(6):
            grid.addWidget(QLabel(f"Item {item_slot + 1}"), 0, item_slot + 2)
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
            grid.addWidget(label, index + 1, 0)
            grid.addWidget(selector, index + 1, 1)

            player_item_selectors = []
            player_item_defaults = item_defaults[index] if index < len(item_defaults) else []
            for item_slot in range(6):
                item_selector = QComboBox()
                item_selector.setMinimumWidth(180)
                item_selector.addItem("No item", 0)
                for item in self._items:
                    label_text = item.name
                    if item.total_cost is not None:
                        label_text = f"{item.name} ({item.total_cost})"
                    item_selector.addItem(label_text, item.item_id)
                if item_slot < len(player_item_defaults):
                    selected_index = item_selector.findData(player_item_defaults[item_slot])
                    if selected_index >= 0:
                        item_selector.setCurrentIndex(selected_index)
                player_item_selectors.append(item_selector)
                grid.addWidget(item_selector, index + 1, item_slot + 2)
            self._item_selectors.append(player_item_selectors)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, alignment=Qt.AlignmentFlag.AlignRight)

    def selected_champion_ids(self) -> list[int]:
        return [int(selector.currentData()) for selector in self._selectors]

    def selected_item_ids_by_player(self) -> list[list[int]]:
        return [
            [int(selector.currentData()) for selector in selectors if selector.currentData()]
            for selectors in self._item_selectors
        ]
