from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt

from league_cast_assist.ui.now_playing_window import NowPlayingWindow


def test_window_title_is_stable_for_obs_sources(tmp_path: Path) -> None:
    """OBS Window Capture re-binds by title, so it must never change."""
    overlay = NowPlayingWindow(tmp_path)
    try:
        assert overlay.WINDOW_TITLE == "LeagueCastAssist — Now Playing (OBS)"
        assert overlay.windowTitle() == overlay.WINDOW_TITLE
    finally:
        overlay.allow_close()
        overlay.close()


def test_window_has_no_close_or_minimise_button(tmp_path: Path) -> None:
    overlay = NowPlayingWindow(tmp_path)
    try:
        flags = overlay.windowFlags()
        assert not flags & Qt.WindowType.WindowCloseButtonHint
        assert not flags & Qt.WindowType.WindowMinimizeButtonHint
    finally:
        overlay.allow_close()
        overlay.close()


def test_window_survives_a_normal_close_attempt(qapp, tmp_path) -> None:  # noqa: ANN001
    overlay = NowPlayingWindow(tmp_path)
    overlay.show()
    qapp.processEvents()

    overlay.close()  # e.g. Alt+F4, or a stray window-manager close
    qapp.processEvents()
    assert overlay.isVisible() is True

    overlay.allow_close()
    overlay.close()
    qapp.processEvents()
    assert overlay.isVisible() is False


def test_idle_state_is_rendered_before_any_track(tmp_path: Path) -> None:
    overlay = NowPlayingWindow(tmp_path)
    try:
        overlay.clear_song()
        assert overlay.title.text() == "Waiting for music"
        assert overlay.time.text() == "0:00 / 0:00"
    finally:
        overlay.allow_close()
        overlay.close()
