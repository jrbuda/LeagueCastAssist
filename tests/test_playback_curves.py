from __future__ import annotations

import pytest

from league_cast_assist.music.playback import CURVES, slider_to_gain


def test_curves_are_monotonic_and_normalised() -> None:
    for curve in CURVES:
        gains = [slider_to_gain(percent, curve) for percent in range(0, 101)]
        assert gains[0] == 0.0, curve
        assert gains[-1] == pytest.approx(1.0), curve
        pairs = zip(gains, gains[1:], strict=False)
        assert all(later >= earlier for earlier, later in pairs), curve


def test_curve_shapes_differ_where_it_matters() -> None:
    # At 50 % the old linear slider is already at half amplitude; every
    # perceptual curve is much further down, which is the reported problem.
    assert slider_to_gain(50, "linear") == pytest.approx(0.5)
    assert slider_to_gain(50, "squared") == pytest.approx(0.25)
    assert slider_to_gain(50, "db40") == pytest.approx(0.1)
    assert slider_to_gain(50, "db60") == pytest.approx(0.0316, abs=0.001)


def test_db_curves_are_gentler_at_the_bottom() -> None:
    for curve in ("db40", "db60"):
        assert slider_to_gain(10, curve) < slider_to_gain(10, "linear") / 5
        # A 5 % move near 25 % must be a small relative step, not a doubling.
        low = slider_to_gain(25, curve)
        high = slider_to_gain(30, curve)
        assert high / low < 1.6


def test_unknown_curve_falls_back_to_db40() -> None:
    assert slider_to_gain(50, "nonsense") == pytest.approx(slider_to_gain(50, "db40"))


def test_slider_bounds_are_clamped() -> None:
    assert slider_to_gain(-20, "db40") == 0.0
    assert slider_to_gain(250, "db40") == pytest.approx(1.0)


def test_defaults_match_the_approved_plan() -> None:
    from league_cast_assist.config import MusicSettings

    settings = MusicSettings()
    assert settings.volume_curve == "db40"
    assert settings.volume == 80
    assert settings.fade_enabled is True
    assert settings.fade_ms == 200
    assert settings.autoplay_after_manual is True
