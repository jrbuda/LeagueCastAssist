"""Track selection rules, extracted verbatim from the standalone music player.

Pure functions only: no Qt, no SQLite, no I/O, so the repeat/weighting behaviour
can be tested directly.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence

from league_cast_assist.music.models import Song

MIN_INTERVENING_SONGS = 5


def intervening_count(song_id: int, history: Sequence[int]) -> int | None:
    """Number of OTHER songs played since this track last began.

    ``None`` means the track has not been played in this session.
    """
    for offset, previous_id in enumerate(reversed(history)):
        if previous_id == song_id:
            return offset
    return None


def repeat_eligible(
    song_id: int,
    history: Sequence[int],
    minimum: int = MIN_INTERVENING_SONGS,
) -> bool:
    gap = intervening_count(song_id, history)
    return gap is None or gap >= minimum


def cooldown_remaining(
    song_id: int,
    history: Sequence[int],
    minimum: int = MIN_INTERVENING_SONGS,
) -> int:
    gap = intervening_count(song_id, history)
    if gap is None or gap >= minimum:
        return 0
    return minimum - gap


def eligible_shuffle_songs(
    songs: Iterable[Song],
    history: Sequence[int],
    pending_id: int | None = None,
    minimum: int = MIN_INTERVENING_SONGS,
) -> list[Song]:
    return [
        song
        for song in songs
        if song.playback_mode == "shuffle"
        and song.song_id != pending_id
        and repeat_eligible(song.song_id, history, minimum)
    ]


def vote_weight(votes: int) -> float:
    return 1.0 + math.sqrt(max(votes, 0))


def freshness_weight(gap: int | None, minimum: int = MIN_INTERVENING_SONGS) -> float:
    """Hard floor at ``minimum`` other songs; soft preference after that."""
    if gap is None:
        return 1.0
    return min(1.0, 0.15 + 0.17 * (gap - minimum))


def pick_weighted(
    songs: Iterable[Song],
    history: Sequence[int],
    votes: dict[int, int] | None = None,
    pending_id: int | None = None,
    minimum: int = MIN_INTERVENING_SONGS,
    rng: random.Random | None = None,
) -> Song | None:
    """Vote-weighted random pick with a five-song repeat floor."""
    pool = eligible_shuffle_songs(songs, history, pending_id, minimum)
    if not pool:
        return None

    votes = votes or {}
    weights: list[float] = []
    for song in pool:
        gap = intervening_count(song.song_id, history)
        weights.append(
            vote_weight(votes.get(song.song_id, 0)) * freshness_weight(gap, minimum)
        )

    chooser = rng or random
    return chooser.choices(pool, weights=weights, k=1)[0]


def group_songs(songs: Iterable[Song], group_name: str) -> list[Song]:
    return [song for song in songs if song.group_name == group_name]


def status_text(
    song: Song,
    history: Sequence[int],
    minimum: int = MIN_INTERVENING_SONGS,
) -> str:
    """Short library status label, matching the standalone app's wording."""
    if song.playback_mode == "disabled":
        return "Disabled"
    if song.playback_mode == "manual":
        return "Manual (no cooldown)"
    gap = intervening_count(song.song_id, history)
    remaining = cooldown_remaining(song.song_id, history, minimum)
    if remaining:
        return f"Cooldown ({remaining} more)"
    if gap is not None:
        return f"Repeat ready ({gap} apart)"
    return "Shuffle"
