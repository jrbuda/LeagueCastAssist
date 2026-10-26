from __future__ import annotations

from league_cast_assist.data.game_activity import ActivityEvent, GameActivityTracker, is_in_game
from league_cast_assist.models import MatchState


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def state(source: str = "lcu", phase: str = "Lobby") -> MatchState:
    return MatchState(source=source, phase=phase)


def test_live_client_source_is_always_in_game() -> None:
    assert is_in_game(state(source="liveclient", phase="Anything")) is True
    assert is_in_game(state(source="lcu", phase="InProgress")) is True
    assert is_in_game(state(source="lcu", phase="GameStart")) is True
    assert is_in_game(state(source="lcu", phase="ChampSelect")) is False
    assert is_in_game(state(source="debug", phase="InProgress")) is False


def test_game_start_emits_once() -> None:
    clock = FakeClock()
    tracker = GameActivityTracker(idle_return_delay_seconds=8, clock=clock)

    assert tracker.update(state()) is None
    assert tracker.update(state(phase="ChampSelect")) is None
    assert tracker.update(state(phase="InProgress")) is ActivityEvent.GAME_STARTED
    assert tracker.update(state(phase="InProgress")) is None
    assert tracker.in_game is True


def test_game_end_waits_for_the_grace_period() -> None:
    clock = FakeClock()
    tracker = GameActivityTracker(idle_return_delay_seconds=8, clock=clock)
    tracker.update(state(phase="InProgress"))

    assert tracker.update(state(phase="EndOfGame")) is None
    clock.advance(4)
    assert tracker.update(state(phase="EndOfGame")) is None
    clock.advance(4)
    assert tracker.update(state(phase="EndOfGame")) is ActivityEvent.GAME_ENDED
    assert tracker.in_game is False


def test_reconnect_within_the_grace_period_is_silent() -> None:
    clock = FakeClock()
    tracker = GameActivityTracker(idle_return_delay_seconds=8, clock=clock)
    tracker.update(state(phase="InProgress"))

    assert tracker.update(state(phase="Reconnect")) is None
    clock.advance(3)
    assert tracker.update(state(phase="InProgress")) is None
    clock.advance(30)
    assert tracker.update(state(phase="InProgress")) is None
    assert tracker.in_game is True


def test_loading_screen_blip_does_not_end_the_game() -> None:
    clock = FakeClock()
    tracker = GameActivityTracker(idle_return_delay_seconds=8, clock=clock)
    tracker.update(state(source="liveclient"))

    assert tracker.update(state(source="lcu", phase="GameStart")) is None
    clock.advance(2)
    assert tracker.update(state(source="liveclient")) is None
    assert tracker.in_game is True


def test_zero_delay_ends_immediately() -> None:
    clock = FakeClock()
    tracker = GameActivityTracker(idle_return_delay_seconds=0, clock=clock)
    tracker.update(state(phase="InProgress"))
    assert tracker.update(state(phase="Lobby")) is ActivityEvent.GAME_ENDED


def test_tick_resolves_the_deadline_between_polls() -> None:
    clock = FakeClock()
    tracker = GameActivityTracker(idle_return_delay_seconds=5, clock=clock)
    tracker.update(state(phase="InProgress"))
    tracker.update(state(phase="EndOfGame"))

    clock.advance(5)
    assert tracker.tick() is ActivityEvent.GAME_ENDED
    assert tracker.tick() is None
    assert tracker.in_game is False


def test_idle_delay_can_be_changed_at_runtime() -> None:
    clock = FakeClock()
    tracker = GameActivityTracker(idle_return_delay_seconds=60, clock=clock)
    tracker.update(state(phase="InProgress"))
    tracker.update(state(phase="Lobby"))
    tracker.set_idle_delay(2)
    clock.advance(3)
    assert tracker.tick() is ActivityEvent.GAME_ENDED
