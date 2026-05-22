from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from league_cast_assist.models import ItemValueSample, MatchState, ObjectiveEvent, PlayerState


class ItemValueGraphPanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("GraphPanel")
        self._mode = "item_team"
        self._debug_objectives_visible = False
        self._item_team_button: QRadioButton | None = None
        self._objective_button: QRadioButton | None = None
        self._selected_players: dict[str, PlayerState] = {}
        self._state = MatchState()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(3)
        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("Match Graphs")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch()

        self._buttons = QButtonGroup(self)
        for mode, label in (
            ("item_team", "Item Value"),
            ("item_player", "Player Value"),
            ("kills", "Kills"),
        ):
            button = QRadioButton(label)
            button.setChecked(mode == self._mode)
            button.toggled.connect(lambda checked, m=mode: self._set_mode(m) if checked else None)
            self._buttons.addButton(button)
            header.addWidget(button)
            if mode == "item_team":
                self._item_team_button = button

        self._objective_button = QRadioButton("Objectives")
        self._objective_button.setVisible(False)
        self._objective_button.toggled.connect(
            lambda checked: self._set_mode("objectives") if checked else None
        )
        self._buttons.addButton(self._objective_button)
        header.addWidget(self._objective_button)
        layout.addLayout(header)

        self._subtitle = QLabel("Waiting for graph samples")
        self._subtitle.setObjectName("Muted")
        layout.addWidget(self._subtitle)

        self._player_picker = QWidget()
        self._player_picker_layout = QGridLayout(self._player_picker)
        self._player_picker_layout.setContentsMargins(0, 0, 0, 0)
        self._player_picker_layout.setHorizontalSpacing(4)
        self._player_picker_layout.setVerticalSpacing(1)
        layout.addWidget(self._player_picker)

        self._graph = MatchGraph()
        layout.addWidget(self._graph, stretch=1)
        self._update_player_picker()

    def update_state(self, state: MatchState) -> None:
        self._state = state
        self._selected_players = {
            key: player
            for key, player in self._selected_players.items()
            if any(current.stable_key == key for current in state.players)
        }
        self._graph.set_data(
            state.item_value_samples,
            state.objective_events,
            self._mode,
            list(self._selected_players.values()),
        )
        self._update_player_picker()
        self._subtitle.setText(self._subtitle_text())

    def set_selected_player(self, player: PlayerState) -> None:
        if player.stable_key in self._selected_players:
            del self._selected_players[player.stable_key]
        else:
            self._selected_players[player.stable_key] = player
        self._graph.set_selected_players(list(self._selected_players.values()))
        self._update_player_picker()
        self._subtitle.setText(self._subtitle_text())

    def set_debug_objectives_visible(self, visible: bool) -> None:
        self._debug_objectives_visible = visible
        if self._objective_button is not None:
            self._objective_button.setVisible(visible)
            if visible:
                self._objective_button.setChecked(True)
        if not visible and self._mode == "objectives":
            self._mode = "item_team"
            if self._item_team_button is not None:
                self._item_team_button.setChecked(True)
            self._graph.set_mode(self._mode)
            self._update_player_picker()
            self._subtitle.setText(self._subtitle_text())

    def _set_mode(self, mode: str) -> None:
        if mode == "objectives" and not self._debug_objectives_visible:
            return
        self._mode = mode
        self._graph.set_mode(mode)
        self._update_player_picker()
        self._subtitle.setText(self._subtitle_text())

    def _subtitle_text(self) -> str:
        if self._mode == "item_team":
            return "Blue team total visible item value vs red team total visible item value"
        if self._mode == "item_player":
            if self._selected_players:
                names = ", ".join(player.display_name for player in self._selected_players.values())
                return f"Comparing selected player item value: {names}"
            return "Select one or more player cards to compare individual item value"
        if self._mode == "kills":
            return "Team kill totals over game time"
        return "Towers, dragons, epic monsters, and inhibitors taken"

    def _update_player_picker(self) -> None:
        clear_layout(self._player_picker_layout)
        self._player_picker.setVisible(self._mode == "item_player")
        if self._mode != "item_player":
            return

        for player in self._state.players:
            label = player.display_name
            if player.champion_name:
                label = f"{player.display_name} ({player.champion_name})"
            checkbox = QCheckBox(label)
            checkbox.setChecked(player.stable_key in self._selected_players)
            checkbox.toggled.connect(
                lambda checked, p=player: self._set_player_checked(p, checked)
            )
            index = self._player_picker_layout.count()
            self._player_picker_layout.addWidget(checkbox, index // 2, index % 2)

    def _set_player_checked(self, player: PlayerState, checked: bool) -> None:
        if checked:
            self._selected_players[player.stable_key] = player
        else:
            self._selected_players.pop(player.stable_key, None)
        self._graph.set_selected_players(list(self._selected_players.values()))
        self._subtitle.setText(self._subtitle_text())


class MatchGraph(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._samples: list[ItemValueSample] = []
        self._objective_events: list[ObjectiveEvent] = []
        self._mode = "item_team"
        self._selected_players: list[PlayerState] = []
        self.setMinimumHeight(105)

    def set_data(
        self,
        samples: list[ItemValueSample],
        objective_events: list[ObjectiveEvent],
        mode: str,
        selected_players: list[PlayerState],
    ) -> None:
        self._samples = samples
        self._objective_events = objective_events
        self._mode = mode
        self._selected_players = selected_players
        self.update()

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self.update()

    def set_selected_players(self, players: list[PlayerState]) -> None:
        self._selected_players = players
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(38, 8, -12, -22)
        painter.fillRect(self.rect(), QColor("#12161d"))
        painter.setPen(QPen(QColor("#303846"), 1))
        painter.drawRect(rect)

        if self._mode == "objectives":
            self._draw_objective_timeline(painter, rect)
            return

        if len(self._samples) < 2:
            painter.setPen(QColor("#9aa4b2"))
            message = "Graph will populate in game"
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, message)
            return

        series = self._series()
        max_value = max((value for _, values, _ in series for value in values), default=0)
        if max_value <= 0:
            painter.setPen(QColor("#9aa4b2"))
            message = "No data for this graph yet"
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, message)
            return

        start_time = self._samples[0].game_time_seconds
        end_time = max(sample.game_time_seconds for sample in self._samples)
        if end_time <= start_time:
            end_time = start_time + 1

        self._draw_grid(painter, rect, max_value)
        for index, (label, values, color) in enumerate(series):
            points = []
            for sample, value in zip(self._samples, values, strict=False):
                x_ratio = (sample.game_time_seconds - start_time) / (end_time - start_time)
                y_ratio = value / max_value
                points.append(
                    QPointF(
                        rect.left() + x_ratio * rect.width(),
                        rect.bottom() - y_ratio * rect.height(),
                    )
                )
            self._draw_polyline(painter, points, color)
            self._draw_legend(painter, label, color, index)

    def _series(self) -> list[tuple[str, list[int], QColor]]:
        if self._mode == "item_player":
            return [
                (
                    player.display_name,
                    [sample.player_values.get(player.stable_key, 0) for sample in self._samples],
                    graph_color(index),
                )
                for index, player in enumerate(self._selected_players)
            ]
        if self._mode == "kills":
            return [
                ("Blue Kills", [sample.blue_kills for sample in self._samples], QColor("#4c8dff")),
                ("Red Kills", [sample.red_kills for sample in self._samples], QColor("#e35d6a")),
            ]
        return [
            ("Blue Value", [sample.blue_total for sample in self._samples], QColor("#4c8dff")),
            ("Red Value", [sample.red_total for sample in self._samples], QColor("#e35d6a")),
        ]

    def _draw_grid(self, painter: QPainter, rect, max_value: int) -> None:  # noqa: ANN001
        painter.setPen(QPen(QColor("#253040"), 1))
        for index in range(1, 4):
            y = rect.top() + rect.height() * index / 4
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))

        painter.setPen(QColor("#9aa4b2"))
        painter.drawText(4, rect.top() + 8, str(max_value))
        painter.drawText(4, rect.bottom(), "0")

    def _draw_polyline(self, painter: QPainter, points: list[QPointF], color: QColor) -> None:
        if len(points) < 2:
            return
        painter.setPen(QPen(color, 2))
        for first, second in zip(points, points[1:], strict=False):
            painter.drawLine(first, second)

    def _draw_legend(self, painter: QPainter, label: str, color: QColor, index: int) -> None:
        x = 48 + (index % 3) * 170
        y = self.height() - 20 + (index // 3) * 10
        painter.setPen(QPen(color, 3))
        painter.drawLine(x, y - 4, x + 22, y - 4)
        painter.setPen(QColor("#e6eaf2"))
        painter.drawText(x + 28, y, label)

    def _draw_objective_timeline(self, painter: QPainter, rect) -> None:  # noqa: ANN001
        events = self._objective_events
        if not events:
            painter.setPen(QColor("#9aa4b2"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No objective events yet")
            return

        start_time = 0.0
        end_time = max(
            [event.game_time_seconds for event in events]
            + [sample.game_time_seconds for sample in self._samples]
            + [1.0]
        )
        if end_time <= start_time:
            end_time = 1.0

        blue_y = rect.top() + rect.height() * 0.33
        red_y = rect.top() + rect.height() * 0.67
        painter.setPen(QPen(QColor("#4c8dff"), 2))
        painter.drawLine(rect.left(), int(blue_y), rect.right(), int(blue_y))
        painter.setPen(QPen(QColor("#e35d6a"), 2))
        painter.drawLine(rect.left(), int(red_y), rect.right(), int(red_y))
        painter.setPen(QColor("#9aa4b2"))
        painter.drawText(4, int(blue_y + 4), "Blue")
        painter.drawText(4, int(red_y + 4), "Red")

        font = painter.font()
        font.setPointSize(max(8, font.pointSize() - 1))
        font.setBold(True)
        painter.setFont(font)
        for index, event in enumerate(events):
            x_ratio = (event.game_time_seconds - start_time) / (end_time - start_time)
            x = rect.left() + x_ratio * rect.width()
            y = blue_y if event.team_side == "blue" else red_y
            color = QColor("#4c8dff") if event.team_side == "blue" else QColor("#e35d6a")
            self._draw_objective_marker(painter, x, y, objective_icon(event.objective_type), color)
            if index % 2 == 0:
                painter.setPen(QColor("#9aa4b2"))
                painter.drawText(
                    int(x - 16),
                    int(y - 18),
                    format_game_time(event.game_time_seconds),
                )

        self._draw_objective_legend(painter)

    def _draw_objective_marker(
        self,
        painter: QPainter,
        x: float,
        y: float,
        icon: str,
        color: QColor,
    ) -> None:
        painter.setPen(QPen(color, 2))
        painter.setBrush(QColor("#12161d"))
        painter.drawEllipse(QPointF(x, y), 11, 11)
        painter.setPen(QColor("#e6eaf2"))
        painter.drawText(int(x - 7), int(y + 5), icon)

    def _draw_objective_legend(self, painter: QPainter) -> None:
        painter.setPen(QColor("#9aa4b2"))
        painter.drawText(
            48,
            self.height() - 8,
            "T Tower | D Dragon | B Baron | H Herald | V Voidgrub | A Atakhan | I Inhib",
        )


def graph_color(index: int) -> QColor:
    palette = [
        "#d6b35a",
        "#70d6ff",
        "#80d878",
        "#ff9966",
        "#c792ea",
        "#f07178",
        "#89ddff",
        "#ffcb6b",
        "#82aaff",
        "#c3e88d",
    ]
    return QColor(palette[index % len(palette)])


def objective_icon(objective_type: str) -> str:
    if objective_type.endswith("_dragon") or objective_type == "dragon":
        return "D"
    return {
        "tower": "T",
        "baron": "B",
        "herald": "H",
        "voidgrub": "V",
        "atakhan": "A",
        "inhibitor": "I",
    }.get(objective_type, "O")


def format_game_time(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d}"


def clear_layout(layout: QHBoxLayout | QVBoxLayout | QGridLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
