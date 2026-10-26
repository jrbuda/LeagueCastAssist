from __future__ import annotations

from PySide6.QtWidgets import QWidget

from league_cast_assist.ui.detachable_tabs import DetachableTabWidget, clamp_geometry


def make_page(title: str) -> QWidget:
    page = QWidget()
    page.page_title = title  # type: ignore[attr-defined]
    page.setObjectName(title)
    return page


def test_tab_starts_docked(qapp) -> None:  # noqa: ANN001
    tabs = DetachableTabWidget()
    page = make_page("Match")
    tabs.addTab(page, "Match")

    assert tabs.count() == 1
    assert tabs.is_detached(page) is False
    assert tabs.detached_window(page) is None
    tabs.close()


def test_detach_moves_the_same_page_instance(qapp) -> None:  # noqa: ANN001
    tabs = DetachableTabWidget()
    match_page = make_page("Match")
    music_page = make_page("Music")
    tabs.addTab(match_page, "Match")
    tabs.addTab(music_page, "Music")

    window = tabs.detach(1)

    assert window is not None
    assert tabs.count() == 1
    assert tabs.widget(0) is match_page
    assert tabs.is_detached(music_page) is True
    assert tabs.detached_window(music_page) is window
    assert music_page.isVisible() is True
    assert window.windowTitle().endswith("Music")
    window.close()
    tabs.close()


def test_closing_the_detached_window_redocks_the_page(qapp) -> None:  # noqa: ANN001
    tabs = DetachableTabWidget()
    page = make_page("Music")
    tabs.addTab(page, "Music")

    window = tabs.detach_page(page)
    assert window is True

    tabs.detached_window(page).close()

    assert tabs.is_detached(page) is False
    assert tabs.count() == 1
    assert tabs.widget(0) is page
    tabs.close()


def test_redock_all_closes_every_detached_page(qapp) -> None:  # noqa: ANN001
    tabs = DetachableTabWidget()
    first = make_page("Match")
    second = make_page("Music")
    tabs.addTab(first, "Match")
    tabs.addTab(second, "Music")
    tabs.detach(0)
    tabs.detach(0)
    assert tabs.count() == 0

    tabs.redock_all()

    assert tabs.count() == 2
    assert sorted(tabs.detached_pages()) == []
    tabs.close()


def test_geometry_restore_is_clamped_to_a_visible_screen(qapp) -> None:  # noqa: ANN001
    assert clamp_geometry(None) is None
    assert clamp_geometry([1, 2, 3]) is None

    far_away = clamp_geometry([-40_000, -40_000, 900, 600])
    assert far_away is not None
    assert far_away[2] == 900
    assert -20_000 < far_away[0] < 20_000

    small = clamp_geometry([100, 100, 10, 10])
    assert small is not None
    assert small[2] >= 640
    assert small[3] >= 420
