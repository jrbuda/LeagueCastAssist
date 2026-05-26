from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from league_cast_assist.models import AbilityState, ItemState, PlayerState
from league_cast_assist.ui.image_loader import ImageLoader


class DetailPanel(QFrame):
    def __init__(self, image_loader: ImageLoader) -> None:
        super().__init__()
        self._image_loader = image_loader
        self._current_icon: str | None = None
        self.setObjectName("DetailPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(4)
        header = QHBoxLayout()
        header.setSpacing(6)

        self._icon = QLabel()
        self._icon.setFixedSize(44, 44)
        self._icon.setObjectName("LargeIcon")
        header.addWidget(self._icon)

        header_text = QVBoxLayout()
        header_text.setSpacing(1)
        self._title = QLabel("Select an ability or item")
        self._title.setObjectName("DetailTitle")
        self._subtitle = QLabel("Click an ability or item to inspect details.")
        self._subtitle.setObjectName("Muted")
        header_text.addWidget(self._title)
        header_text.addWidget(self._subtitle)
        header.addLayout(header_text, stretch=1)
        layout.addLayout(header)

        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        detail_content = QWidget()
        detail_layout = QVBoxLayout(detail_content)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(4)

        self._meta = QLabel("")
        self._meta.setTextFormat(Qt.TextFormat.RichText)
        self._meta.setObjectName("Muted")
        self._meta.setWordWrap(True)
        detail_layout.addWidget(self._meta)

        self._description = QLabel("")
        self._description.setTextFormat(Qt.TextFormat.RichText)
        self._description.setWordWrap(True)
        self._description.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._description.setObjectName("DetailDescription")
        detail_layout.addWidget(self._description, stretch=1)
        detail_scroll.setWidget(detail_content)
        layout.addWidget(detail_scroll, stretch=1)

        self._image_loader.loaded.connect(self._on_image_loaded)

    def show_ability(self, player: PlayerState, ability: AbilityState) -> None:
        self._title.setText(ability.name)
        self._subtitle.setText(f"{player.champion_name or 'Champion'} - {ability.slot}")
        metadata = []
        if ability.cooldown:
            metadata.append(f"Cooldown: {ability.cooldown}")
        if ability.cost:
            metadata.append(f"Cost: {ability.cost}")
        if ability.range:
            metadata.append(f"Range: {ability.range}")
        metadata.extend(ability.stat_lines)
        self._meta.setText("<br>".join(metadata))
        self._description.setText(
            ability.tooltip_html or ability.full_description or ability.short_description or ""
        )
        self._set_icon(ability.icon)

    def show_item(self, player: PlayerState, item: ItemState) -> None:
        self._title.setText(item.name)
        self._subtitle.setText(player.display_name)
        metadata = []
        if item.total_cost is not None:
            metadata.append(f"Item value: {item.total_cost}")
        if item.count > 1:
            metadata.append(f"Count: {item.count}")
        self._meta.setText(" | ".join(metadata))
        self._description.setText(item.tooltip_html or item.description or "")
        self._set_icon(item.icon)

    def show_rune(self, player: PlayerState) -> None:
        keystone = player.rune_keystone or "Unknown Keystone"
        self._title.setText(keystone)
        champion = player.champion_name or player.display_name
        self._subtitle.setText(f"{champion} · Runes")
        meta_parts: list[str] = []
        if player.rune_primary_tree:
            meta_parts.append(f"Primary: {player.rune_primary_tree}")
        if player.rune_secondary_tree:
            meta_parts.append(f"Secondary: {player.rune_secondary_tree}")
        self._meta.setText(" | ".join(meta_parts))
        if player.rune_keystone:
            desc_lines = [
                f"<b>Keystone:</b> {player.rune_keystone}",
            ]
            if player.rune_primary_tree:
                desc_lines.append(f"<b>Primary tree:</b> {player.rune_primary_tree}")
            if player.rune_secondary_tree:
                desc_lines.append(f"<b>Secondary tree:</b> {player.rune_secondary_tree}")
            desc_lines.append(
                "<br><i>Individual rune picks within each tree are not available "
                "in spectator mode.</i>"
            )
            self._description.setText("<br>".join(desc_lines))
        else:
            self._description.setText("<i>Rune data not yet available.</i>")
        self._set_icon(None)

    def _set_icon(self, source: str | None) -> None:
        self._current_icon = source
        self._icon.clear()
        self._icon.setText("No icon")
        if not source:
            return

        pixmap = self._image_loader.load(source)
        if pixmap is not None:
            self._apply_icon(pixmap)

    def refresh_icon(self) -> None:
        self._set_icon(self._current_icon)

    def _on_image_loaded(self, source: str, pixmap: QPixmap) -> None:
        if source == self._current_icon:
            self._apply_icon(pixmap)

    def _apply_icon(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        self._icon.setText("")
        self._icon.setPixmap(
            pixmap.scaled(
                self._icon.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
