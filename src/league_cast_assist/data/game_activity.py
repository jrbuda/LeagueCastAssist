"""In-game detection with hysteresis.

Fed the ``MatchState`` the app already polls, this tracker emits exactly one
event per "a game started" / "a game ended" transition. It is deliberately free
of Qt and of wall-clock globals so the noisy sequences (loading screens,
reconnects, spectator lag) can be unit tested.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from time import monotonic

from league_cast_assist.models import MatchState

IN_GAME_PHASES = frozenset({"InProgress", "GameStart", "Reconnect"})


class ActivityEvent(str, Enum):
    GAME_STARTED = "started"
    GAME_ENDED = "ended"


def is_in_game(state: MatchState) -> bool:
    if state.source == "debug":
        return False
    if state.source == "liveclient":
        return True
    return (state.phase or "") in IN_GAME_PHASES


class GameActivityTracker:
    def __init__(
        self,
        idle_return_delay_seconds: float = 8.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._delay = max(0.0, float(idle_return_delay_seconds))
        self._clock = clock
        self._in_game = False
        self._idle_deadline: float | None = None
        self._idle_since: float | None = None
        self._started_once = False

    @property
    def in_game(self) -> bool:
        return self._in_game

    @property
    def seen_any_state(self) -> bool:
        return self._started_once

    def set_idle_delay(self, seconds: float) -> None:
        self._delay = max(0.0, float(seconds))
        # A settings change takes effect on a grace period already in flight.
        if self._idle_since is not None:
            self._idle_deadline = self._idle_since + self._delay

    def reset(self) -> None:
        self._in_game = False
        self._idle_deadline = None
        self._idle_since = None

    def update(self, state: MatchState) -> ActivityEvent | None:
        """Return a transition event, or ``None`` when nothing changed."""
        self._started_once = True
        now = self._clock()

        if is_in_game(state):
            self._idle_deadline = None
            self._idle_since = None
            if not self._in_game:
                self._in_game = True
                return ActivityEvent.GAME_STARTED
            return None

        if not self._in_game:
            return None

        # Grace period: loading screens and reconnects look like "game over"
        # for a few seconds and must not bounce the user out of the match view.
        if self._idle_deadline is None:
            if self._delay <= 0:
                self._in_game = False
                return ActivityEvent.GAME_ENDED
            self._idle_since = now
            self._idle_deadline = now + self._delay
            return None

        if now >= self._idle_deadline:
            self._in_game = False
            self._idle_deadline = None
            self._idle_since = None
            return ActivityEvent.GAME_ENDED
        return None

    def tick(self) -> ActivityEvent | None:
        """Let a timer resolve an expiring grace period between polls."""
        if self._in_game and self._idle_deadline is not None:
            if self._clock() >= self._idle_deadline:
                self._in_game = False
                self._idle_deadline = None
                self._idle_since = None
                return ActivityEvent.GAME_ENDED
        return None
