from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from league_cast_assist.models import ItemValueSample
from league_cast_assist.ui.graph import GraphSeries, MatchGraph, hover_candidate_segments


def test_hovered_series_handles_equal_segment_distances() -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None

    graph = MatchGraph()
    graph._samples = [
        ItemValueSample(game_time_seconds=0, blue_total=0, red_total=0),
        ItemValueSample(game_time_seconds=60, blue_total=0, red_total=0),
        ItemValueSample(game_time_seconds=120, blue_total=0, red_total=0),
    ]
    graph._hover_pos = QPoint(50, 50)
    series = GraphSeries("Flat", [1, 1, 1], QColor("#ffffff"))
    points = [QPointF(0, 50), QPointF(50, 50), QPointF(100, 50)]

    hover = graph._hovered_series([(series, points)], QRect(0, 0, 100, 100))

    assert hover is not None
    assert hover[0] is series


def test_hover_candidate_segments_checks_only_nearby_lines() -> None:
    points = [QPointF(x, 0) for x in range(0, 1000, 10)]

    segments = list(hover_candidate_segments(points, 455))

    assert segments == [44, 45, 46]


def test_set_data_skips_repaint_when_graph_data_is_unchanged() -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None

    graph = MatchGraph()
    samples = [
        ItemValueSample(game_time_seconds=0, blue_total=100, red_total=100),
        ItemValueSample(game_time_seconds=60, blue_total=200, red_total=150),
    ]
    graph.update = update_counter = UpdateCounter()  # type: ignore[method-assign]

    graph.set_data(samples, [], "item_team", [], [], "team")
    graph.set_data(samples, [], "item_team", [], [], "team")

    assert update_counter.count == 1


class UpdateCounter:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1
