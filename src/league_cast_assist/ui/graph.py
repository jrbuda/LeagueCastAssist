from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
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

ROLE_ORDER = {
    "TOP": 0,
    "JUNGLE": 1,
    "MIDDLE": 2,
    "MID": 2,
    "BOTTOM": 3,
    "BOT": 3,
    "UTILITY": 4,
    "SUPPORT": 4,
}
ROLE_LABELS = {
    "TOP": "Top",
    "JUNGLE": "Jungle",
    "MIDDLE": "Mid",
    "MID": "Mid",
    "BOTTOM": "Bot",
    "BOT": "Bot",
    "UTILITY": "Support",
    "SUPPORT": "Support",
}
BLUE_COLOR = QColor("#4c8dff")
RED_COLOR = QColor("#e35d6a")
ROLE_COLORS = {
    0: QColor("#d6b35a"),
    1: QColor("#70d6ff"),
    2: QColor("#c792ea"),
    3: QColor("#ff9966"),
    4: QColor("#80d878"),
}
TIME_TICK_SECONDS = 180


@dataclass(frozen=True)
class GraphSeries:
    label: str
    values: list[int]
    color: QColor
    player: PlayerState | None = None


class ItemValueGraphPanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("GraphPanel")
        self._mode = "item_team"
        self._kills_mode = "team"
        self._debug_objectives_visible = False
        self._item_team_button: QRadioButton | None = None
        self._objective_button: QRadioButton | None = None
        self._selected_players: dict[str, PlayerState] = {}
        self._state = MatchState()
        self._picker_signature: tuple[object, ...] | None = None

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

        self._kills_controls = QWidget()
        kills_layout = QHBoxLayout(self._kills_controls)
        kills_layout.setContentsMargins(0, 0, 0, 0)
        kills_layout.setSpacing(8)
        kills_layout.addWidget(QLabel("Kills:"))
        kills_group = QButtonGroup(self)
        for kills_mode, label in (("team", "Team totals"), ("player", "Per player")):
            button = QRadioButton(label)
            button.setChecked(kills_mode == self._kills_mode)
            button.toggled.connect(
                lambda checked, m=kills_mode: self._set_kills_mode(m) if checked else None
            )
            kills_group.addButton(button)
            kills_layout.addWidget(button)
        kills_layout.addStretch()
        layout.addWidget(self._kills_controls)

        self._player_picker = QWidget()
        self._player_picker_layout = QGridLayout(self._player_picker)
        self._player_picker_layout.setContentsMargins(0, 0, 0, 0)
        self._player_picker_layout.setHorizontalSpacing(8)
        self._player_picker_layout.setVerticalSpacing(1)
        layout.addWidget(self._player_picker)

        self._graph = MatchGraph()
        layout.addWidget(self._graph, stretch=1)
        self._update_controls()

    def update_state(self, state: MatchState) -> None:
        self._state = state
        current_keys = {player.stable_key for player in state.players}
        self._selected_players = {
            key: player for key, player in self._selected_players.items() if key in current_keys
        }
        self._graph.set_data(
            state.item_value_samples,
            state.objective_events,
            self._mode,
            self._selected_player_list(),
            ordered_match_players(state),
            self._kills_mode,
        )
        self._update_controls()
        self._subtitle.setText(self._subtitle_text())

    def set_selected_player(self, player: PlayerState) -> None:
        if player.stable_key in self._selected_players:
            del self._selected_players[player.stable_key]
        else:
            self._selected_players[player.stable_key] = player
        self._graph.set_selected_players(self._selected_player_list())
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
            self._update_controls()
            self._subtitle.setText(self._subtitle_text())

    def _set_mode(self, mode: str) -> None:
        if mode == "objectives" and not self._debug_objectives_visible:
            return
        self._mode = mode
        self._graph.set_mode(mode)
        self._update_controls()
        self._subtitle.setText(self._subtitle_text())

    def _set_kills_mode(self, mode: str) -> None:
        self._kills_mode = mode
        self._graph.set_kills_mode(mode)
        self._subtitle.setText(self._subtitle_text())

    def _selected_player_list(self) -> list[PlayerState]:
        selected_keys = set(self._selected_players)
        return [
            player
            for player in ordered_match_players(self._state)
            if player.stable_key in selected_keys
        ]

    def _update_controls(self) -> None:
        self._kills_controls.setVisible(self._mode == "kills")
        self._update_player_picker()

    def _subtitle_text(self) -> str:
        if self._mode == "item_team":
            return "Blue team total visible item value vs red team total visible item value"
        if self._mode == "item_player":
            return "Select one or more players to compare individual item value"
        if self._mode == "kills":
            if self._kills_mode == "player":
                return "Kills per player, grouped by team and position"
            return "Team kill totals over game time"
        return "Towers, dragons, epic monsters, and inhibitors taken"

    def _update_player_picker(self) -> None:
        signature = player_picker_signature(self._mode, self._state, self._selected_players)
        if signature == self._picker_signature:
            self._player_picker.setVisible(self._mode == "item_player")
            return
        self._picker_signature = signature
        clear_layout(self._player_picker_layout)
        self._player_picker.setVisible(self._mode == "item_player")
        if self._mode != "item_player":
            return

        for column, (team_label, players) in enumerate(
            (
                ("Blue", ordered_team_players(self._state.blue_team.players)),
                ("Red", ordered_team_players(self._state.red_team.players)),
            )
        ):
            header = QLabel(team_label)
            header.setObjectName("Muted")
            self._player_picker_layout.addWidget(header, 0, column)
            for row, player in enumerate(players, start=1):
                checkbox = QCheckBox(player_picker_label(player))
                checkbox.setChecked(player.stable_key in self._selected_players)
                checkbox.toggled.connect(
                    lambda checked, p=player: self._set_player_checked(p, checked)
                )
                self._player_picker_layout.addWidget(checkbox, row, column)

    def _set_player_checked(self, player: PlayerState, checked: bool) -> None:
        if checked:
            self._selected_players[player.stable_key] = player
        else:
            self._selected_players.pop(player.stable_key, None)
        self._graph.set_selected_players(self._selected_player_list())
        self._subtitle.setText(self._subtitle_text())


class MatchGraph(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._samples: list[ItemValueSample] = []
        self._objective_events: list[ObjectiveEvent] = []
        self._mode = "item_team"
        self._kills_mode = "team"
        self._selected_players: list[PlayerState] = []
        self._players: list[PlayerState] = []
        self._hover_pos: QPoint | None = None
        self._hover_role_index: int | None = None
        self._data_signature: tuple[object, ...] | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)
        self.setMinimumHeight(220)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        next_pos = event.position().toPoint()
        if next_pos != self._hover_pos:
            self._hover_pos = next_pos
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001
        self._hover_pos = None
        self.update()
        super().leaveEvent(event)

    def set_data(
        self,
        samples: list[ItemValueSample],
        objective_events: list[ObjectiveEvent],
        mode: str,
        selected_players: list[PlayerState],
        players: list[PlayerState],
        kills_mode: str,
    ) -> None:
        next_signature = graph_data_signature(
            samples,
            objective_events,
            mode,
            selected_players,
            players,
            kills_mode,
        )
        self._samples = samples
        self._objective_events = objective_events
        self._mode = mode
        self._selected_players = selected_players
        self._players = players
        self._kills_mode = kills_mode
        if next_signature != self._data_signature:
            self._data_signature = next_signature
            self.update()

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._data_signature = None
        self.update()

    def set_kills_mode(self, mode: str) -> None:
        self._kills_mode = mode
        self._data_signature = None
        self.update()

    def set_selected_players(self, players: list[PlayerState]) -> None:
        self._selected_players = players
        self._data_signature = None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#12161d"))

        if self._mode == "kills" and self._kills_mode == "player":
            self._draw_split_player_kills(painter)
            return

        if self._mode == "objectives":
            rect = self._plot_rect(legend_rows=1)
            self._draw_plot_border(painter, rect)
            self._draw_objective_timeline(painter, rect)
            return

        if len(self._samples) < 2:
            rect = self._plot_rect(legend_rows=0)
            self._draw_plot_border(painter, rect)
            painter.setPen(QColor("#9aa4b2"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Graph will populate in game",
            )
            return

        series = self._series()
        max_value = max(
            (value for graph_series in series for value in graph_series.values),
            default=0,
        )
        legend_rows = self._legend_rows(len(series))
        rect = self._plot_rect(legend_rows)
        self._draw_plot_border(painter, rect)
        if max_value <= 0:
            painter.setPen(QColor("#9aa4b2"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "No data for this graph yet",
            )
            return

        start_time = self._samples[0].game_time_seconds
        end_time = max(sample.game_time_seconds for sample in self._samples)
        if end_time <= start_time:
            end_time = start_time + 1

        self._draw_grid(painter, rect, max_value, start_time, end_time)
        series_points = [
            (
                graph_series,
                self._points_for_series(graph_series, rect, max_value, start_time, end_time),
            )
            for graph_series in series
        ]
        hover = self._hovered_series(series_points, rect)
        hovered_series = hover[0] if hover else None
        for graph_series, points in series_points:
            self._draw_polyline(
                painter,
                points,
                graph_series.color,
                width=4 if graph_series is hovered_series else 2,
            )
        self._draw_legend(painter, rect, series)
        if hover:
            self._draw_hover_tag(painter, rect, hover)

    def _series(self) -> list[GraphSeries]:
        if self._mode == "item_player":
            return player_series(
                self._selected_players,
                lambda sample, player: sample.player_values.get(player.stable_key, 0),
                self._samples,
            )
        if self._mode == "kills":
            if self._kills_mode == "player":
                return player_series(
                    self._players,
                    lambda sample, player: sample.player_kills.get(player.stable_key, 0),
                    self._samples,
                )
            return [
                GraphSeries(
                    "Blue Kills",
                    [sample.blue_kills for sample in self._samples],
                    BLUE_COLOR,
                ),
                GraphSeries(
                    "Red Kills",
                    [sample.red_kills for sample in self._samples],
                    RED_COLOR,
                ),
            ]
        return [
            GraphSeries("Blue Value", [sample.blue_total for sample in self._samples], BLUE_COLOR),
            GraphSeries("Red Value", [sample.red_total for sample in self._samples], RED_COLOR),
        ]

    def _draw_split_player_kills(self, painter: QPainter) -> None:
        if len(self._samples) < 2:
            rect = self._plot_rect(legend_rows=0)
            self._draw_plot_border(painter, rect)
            painter.setPen(QColor("#9aa4b2"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Graph will populate in game",
            )
            return

        blue_series = player_series(
            ordered_team_players(
                [player for player in self._players if player.team_side == "blue"]
            ),
            lambda sample, player: sample.player_kills.get(player.stable_key, 0),
            self._samples,
        )
        red_series = player_series(
            ordered_team_players([player for player in self._players if player.team_side == "red"]),
            lambda sample, player: sample.player_kills.get(player.stable_key, 0),
            self._samples,
        )
        layout = self._split_kill_layout()
        hover = self._split_hover(
            (layout[0], blue_series),
            (layout[1], red_series),
        )
        self._hover_role_index = role_index(hover[1][0].player) if hover else None
        for team_label, rect, series in (
            ("Blue", layout[0], blue_series),
            ("Red", layout[1], red_series),
        ):
            self._draw_team_kill_graph(
                painter,
                team_label,
                rect,
                series,
                show_time_labels=team_label == "Red",
            )
        if hover:
            rect, team_hover = hover
            self._draw_hover_tag(painter, rect, team_hover)

    def _split_hover(
        self,
        *team_series: tuple[QRect, list[GraphSeries]],
    ) -> tuple[QRect, tuple[GraphSeries, int, QPointF]] | None:
        for rect, series in team_series:
            max_value = max(
                (value for graph_series in series for value in graph_series.values),
                default=1,
            )
            start_time, end_time = self._sample_time_bounds()
            points = [
                (
                    graph_series,
                    self._points_for_series(graph_series, rect, max_value, start_time, end_time),
                )
                for graph_series in series
            ]
            hover = self._hovered_series(points, rect)
            if hover:
                return rect, hover
        return None

    def _draw_team_kill_graph(
        self,
        painter: QPainter,
        team_label: str,
        rect,  # noqa: ANN001
        series: list[GraphSeries],
        show_time_labels: bool = True,
    ) -> None:
        self._draw_plot_border(painter, rect)

        max_value = max(
            (value for graph_series in series for value in graph_series.values),
            default=0,
        )
        if max_value <= 0:
            max_value = 1
        start_time, end_time = self._sample_time_bounds()

        self._draw_grid(
            painter,
            rect,
            max_value,
            start_time,
            end_time,
            show_time_labels=show_time_labels,
        )
        series_points = [
            (
                graph_series,
                self._points_for_series(graph_series, rect, max_value, start_time, end_time),
            )
            for graph_series in series
        ]
        for graph_series, points in series_points:
            related_hover = role_index(graph_series.player) == self._hover_role_index
            self._draw_polyline(
                painter,
                points,
                graph_series.color,
                width=4 if related_hover else 2,
            )
        self._draw_inline_team_labels(painter, rect, team_label, series)

    def _sample_time_bounds(self) -> tuple[float, float]:
        start_time = self._samples[0].game_time_seconds
        end_time = max(sample.game_time_seconds for sample in self._samples)
        if end_time <= start_time:
            end_time = start_time + 1
        return start_time, end_time

    def _draw_plot_border(self, painter: QPainter, rect) -> None:  # noqa: ANN001
        painter.setPen(QPen(QColor("#303846"), 1))
        painter.drawRect(rect)

    def _draw_grid(
        self,
        painter: QPainter,
        rect,  # noqa: ANN001
        max_value: int,
        start_time: float,
        end_time: float,
        show_time_labels: bool = True,
    ) -> None:
        painter.setPen(QPen(QColor("#253040"), 1))
        for index in range(1, 4):
            y = rect.top() + rect.height() * index / 4
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))

        self._draw_time_axis(painter, rect, start_time, end_time, show_labels=show_time_labels)

        painter.setPen(QColor("#9aa4b2"))
        painter.drawText(4, rect.top() + 8, str(max_value))
        painter.drawText(4, rect.bottom(), "0")

    def _draw_time_axis(
        self,
        painter: QPainter,
        rect,  # noqa: ANN001
        start_time: float,
        end_time: float,
        show_labels: bool = True,
    ) -> None:
        painter.setPen(QPen(QColor("#253040"), 1))
        for tick in graph_time_ticks(start_time, end_time):
            x_ratio = (tick - start_time) / (end_time - start_time)
            x = int(rect.left() + x_ratio * rect.width())
            painter.drawLine(x, rect.top(), x, rect.bottom())
            if show_labels:
                painter.setPen(QColor("#9aa4b2"))
                painter.drawText(x - 14, rect.bottom() + 15, format_game_time(tick))
            painter.setPen(QPen(QColor("#253040"), 1))

    def _points_for_series(
        self,
        graph_series: GraphSeries,
        rect,  # noqa: ANN001
        max_value: int,
        start_time: float,
        end_time: float,
    ) -> list[QPointF]:
        points = []
        for sample, value in zip(self._samples, graph_series.values, strict=False):
            x_ratio = (sample.game_time_seconds - start_time) / (end_time - start_time)
            y_ratio = value / max_value
            points.append(
                QPointF(
                    rect.left() + x_ratio * rect.width(),
                    rect.bottom() - y_ratio * rect.height(),
                )
            )
        return points

    def _hovered_series(
        self,
        series_points: list[tuple[GraphSeries, list[QPointF]]],
        rect,  # noqa: ANN001
    ) -> tuple[GraphSeries, int, QPointF] | None:
        if self._hover_pos is None or not rect.contains(self._hover_pos):
            return None
        nearest = None
        nearest_distance = 12.0
        for graph_series, points in series_points:
            if len(points) < 2:
                continue
            for segment_index in hover_candidate_segments(points, self._hover_pos.x()):
                first = points[segment_index]
                second = points[segment_index + 1]
                distance = distance_to_segment(self._hover_pos, first, second)
                if distance < nearest_distance:
                    nearest_distance = distance
                    point = point_at_hover_x(self._hover_pos.x(), first, second)
                    index = self._sample_index_for_x(point.x(), rect)
                    nearest = (graph_series, index, point)
        return nearest

    def _sample_index_for_x(self, x: float, rect) -> int:  # noqa: ANN001
        start_time = self._samples[0].game_time_seconds
        end_time = max(sample.game_time_seconds for sample in self._samples)
        if end_time <= start_time:
            return 0
        x_ratio = max(0.0, min(1.0, (x - rect.left()) / rect.width()))
        hover_time = start_time + x_ratio * (end_time - start_time)
        sample_index = bisect_left(
            self._samples,
            hover_time,
            key=lambda sample: sample.game_time_seconds,
        )
        if sample_index <= 0:
            return 0
        if sample_index >= len(self._samples):
            return len(self._samples) - 1
        previous_index = sample_index - 1
        previous_delta = abs(self._samples[previous_index].game_time_seconds - hover_time)
        next_delta = abs(self._samples[sample_index].game_time_seconds - hover_time)
        return previous_index if previous_delta <= next_delta else sample_index

    def _draw_polyline(
        self,
        painter: QPainter,
        points: list[QPointF],
        color: QColor,
        width: int = 2,
    ) -> None:
        if len(points) < 2:
            return
        painter.setPen(QPen(color, width))
        painter.drawPolyline(QPolygonF(points))

    def _draw_hover_tag(
        self,
        painter: QPainter,
        rect,  # noqa: ANN001
        hover: tuple[GraphSeries, int, QPointF],
    ) -> None:
        graph_series, sample_index, point = hover
        value = graph_series.values[sample_index]
        time = format_game_time(self._samples[sample_index].game_time_seconds)
        label = f"{graph_series.label}: {value} at {time}"
        metrics = painter.fontMetrics()
        tag_width = metrics.horizontalAdvance(label) + 18
        tag_height = metrics.height() + 8
        x = int(min(max(point.x() + 10, rect.left()), rect.right() - tag_width))
        y = int(max(rect.top(), point.y() - tag_height - 8))
        tag_rect = QRect(x, y, tag_width, tag_height)
        painter.setBrush(QColor("#0d1016"))
        painter.setPen(QPen(graph_series.color, 1))
        painter.drawRoundedRect(tag_rect, 5, 5)
        font = QFont(painter.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#e6eaf2"))
        painter.drawText(tag_rect.adjusted(9, 0, -9, 0), Qt.AlignmentFlag.AlignVCenter, label)

    def _draw_legend(
        self,
        painter: QPainter,
        rect,  # noqa: ANN001
        series: list[GraphSeries],
    ) -> None:
        if not series:
            return
        columns = self._legend_columns()
        column_width = max(120, rect.width() // columns)
        y_start = rect.bottom() + 34
        metrics = painter.fontMetrics()
        for index, graph_series in enumerate(series):
            column = index % columns
            row = index // columns
            x = rect.left() + column * column_width
            y = y_start + row * 14
            painter.setPen(QPen(graph_series.color, 3))
            painter.drawLine(x, y - 4, x + 18, y - 4)
            painter.setPen(QColor("#e6eaf2"))
            display_label = metrics.elidedText(
                graph_series.label,
                Qt.TextElideMode.ElideRight,
                max(40, column_width - 26),
            )
            painter.drawText(x + 24, y, display_label)

    def _draw_inline_team_labels(
        self,
        painter: QPainter,
        rect,  # noqa: ANN001
        team_label: str,
        series: list[GraphSeries],
    ) -> None:
        metrics = painter.fontMetrics()
        x = rect.left()
        y = rect.top() - 4
        team_color = BLUE_COLOR if team_label == "Blue" else RED_COLOR
        painter.setPen(team_color)
        painter.drawText(x, y, team_label)
        x += metrics.horizontalAdvance(team_label) + 10

        for graph_series in series:
            player = graph_series.player
            if player is None:
                continue
            value = graph_series.values[-1] if graph_series.values else 0
            label = f"- {role_label(player)} {value}"
            remaining_width = rect.right() - x
            if remaining_width < 32:
                break
            display_label = metrics.elidedText(
                label,
                Qt.TextElideMode.ElideRight,
                remaining_width,
            )
            painter.setPen(graph_series.color)
            painter.drawText(x, y, display_label)
            x += metrics.horizontalAdvance(display_label) + 8

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

        self._draw_time_axis(painter, rect, start_time, end_time)
        blue_y = rect.top() + rect.height() * 0.33
        red_y = rect.top() + rect.height() * 0.67
        painter.setPen(QPen(BLUE_COLOR, 2))
        painter.drawLine(rect.left(), int(blue_y), rect.right(), int(blue_y))
        painter.setPen(QPen(RED_COLOR, 2))
        painter.drawLine(rect.left(), int(red_y), rect.right(), int(red_y))
        painter.setPen(QColor("#9aa4b2"))
        painter.drawText(4, int(blue_y + 4), "Blue")
        painter.drawText(4, int(red_y + 4), "Red")

        font = painter.font()
        font.setPointSize(max(8, font.pointSize() - 1))
        font.setBold(True)
        painter.setFont(font)
        for event in events:
            x_ratio = (event.game_time_seconds - start_time) / (end_time - start_time)
            x = rect.left() + x_ratio * rect.width()
            y = blue_y if event.team_side == "blue" else red_y
            color = BLUE_COLOR if event.team_side == "blue" else RED_COLOR
            self._draw_objective_marker(painter, x, y, objective_icon(event.objective_type), color)

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

    def _plot_rect(self, legend_rows: int):  # noqa: ANN201
        bottom_margin = 30 + legend_rows * 14
        return self.rect().adjusted(42, 8, -12, -bottom_margin)

    def _split_kill_layout(self) -> tuple[QRect, QRect]:
        available = self.rect().adjusted(42, 18, -12, -22)
        gap = 22
        graph_height = max(58, (available.height() - gap) // 2)
        top_graph = QRect(available.left(), available.top(), available.width(), graph_height)
        bottom_top = top_graph.bottom() + gap
        bottom_graph = QRect(available.left(), bottom_top, available.width(), graph_height)
        return top_graph, bottom_graph

    def _legend_columns(self) -> int:
        return max(1, min(3, max(1, (self.width() - 54) // 170)))

    def _legend_rows(self, series_count: int) -> int:
        if series_count <= 0:
            return 0
        columns = self._legend_columns()
        return (series_count + columns - 1) // columns


def player_series(
    players: list[PlayerState],
    value_for_sample: Callable[[ItemValueSample, PlayerState], int],
    samples: list[ItemValueSample],
) -> list[GraphSeries]:
    return [
        GraphSeries(
            player_graph_label(player),
            [value_for_sample(sample, player) for sample in samples],
            graph_color(player),
            player,
        )
        for player in players
    ]


def ordered_match_players(state: MatchState) -> list[PlayerState]:
    return [
        *ordered_team_players(state.blue_team.players),
        *ordered_team_players(state.red_team.players),
    ]


def ordered_team_players(players: list[PlayerState]) -> list[PlayerState]:
    return sorted(players, key=player_sort_key)


def player_sort_key(player: PlayerState) -> tuple[int, str]:
    return (ROLE_ORDER.get((player.position or "").upper(), 99), player.display_name.lower())


def player_picker_label(player: PlayerState) -> str:
    role = ROLE_LABELS.get((player.position or "").upper())
    champion = player.champion_name or player.display_name
    return f"{role}: {champion}" if role else champion


def role_label(player: PlayerState) -> str:
    return (
        player.champion_name
        or ROLE_LABELS.get((player.position or "").upper())
        or player.display_name
    )


def role_index(player: PlayerState | None) -> int | None:
    if player is None:
        return None
    return ROLE_ORDER.get((player.position or "").upper())


def player_graph_label(player: PlayerState) -> str:
    team = "Blue" if player.team_side == "blue" else "Red"
    return f"{team} {player_picker_label(player)}"


def graph_color(player: PlayerState) -> QColor:
    return ROLE_COLORS.get(ROLE_ORDER.get((player.position or "").upper(), 0), BLUE_COLOR)


def graph_data_signature(
    samples: list[ItemValueSample],
    objective_events: list[ObjectiveEvent],
    mode: str,
    selected_players: list[PlayerState],
    players: list[PlayerState],
    kills_mode: str,
) -> tuple[object, ...]:
    return (
        mode,
        kills_mode,
        tuple(player.stable_key for player in selected_players),
        tuple(player_graph_signature(player) for player in players),
        tuple(sample_graph_signature(sample) for sample in samples),
        tuple(event_graph_signature(event) for event in objective_events),
    )


def player_graph_signature(player: PlayerState) -> tuple[object, ...]:
    return (
        player.stable_key,
        player.display_name,
        player.team_side,
        player.position,
        player.champion_name,
    )


def sample_graph_signature(sample: ItemValueSample) -> tuple[object, ...]:
    return (
        sample.game_time_seconds,
        sample.blue_total,
        sample.red_total,
        sample.blue_kills,
        sample.red_kills,
        tuple(sorted(sample.player_values.items())),
        tuple(sorted(sample.player_kills.items())),
    )


def event_graph_signature(event: ObjectiveEvent) -> tuple[object, ...]:
    return (event.game_time_seconds, event.team_side, event.objective_type, event.label)


def player_picker_signature(
    mode: str,
    state: MatchState,
    selected_players: dict[str, PlayerState],
) -> tuple[object, ...]:
    return (
        mode,
        tuple(player_graph_signature(player) for player in ordered_match_players(state)),
        tuple(sorted(selected_players)),
    )


def distance_to_segment(point: QPoint, start: QPointF, end: QPointF) -> float:
    px = float(point.x())
    py = float(point.y())
    ax = start.x()
    ay = start.y()
    bx = end.x()
    by = end.y()
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    ratio = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    nearest_x = ax + ratio * dx
    nearest_y = ay + ratio * dy
    return math.hypot(px - nearest_x, py - nearest_y)


def point_at_hover_x(x: int, start: QPointF, end: QPointF) -> QPointF:
    if end.x() == start.x():
        return start
    ratio = max(0.0, min(1.0, (x - start.x()) / (end.x() - start.x())))
    return QPointF(
        start.x() + ratio * (end.x() - start.x()),
        start.y() + ratio * (end.y() - start.y()),
    )


def hover_candidate_segments(points: list[QPointF], x: int) -> range:
    if len(points) < 2:
        return range(0)
    insertion_index = bisect_left(points, x, key=lambda point: point.x())
    start = max(0, insertion_index - 2)
    stop = min(len(points) - 1, insertion_index + 1)
    return range(start, stop)


def graph_time_ticks(start_time: float, end_time: float) -> list[float]:
    if end_time <= start_time:
        return [start_time]
    first_tick = math.ceil(start_time / TIME_TICK_SECONDS) * TIME_TICK_SECONDS
    ticks = []
    tick = first_tick
    while tick <= end_time:
        ticks.append(float(tick))
        tick += TIME_TICK_SECONDS
    if not ticks:
        return [start_time, end_time]
    return ticks


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
