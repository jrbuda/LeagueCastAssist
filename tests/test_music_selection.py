from __future__ import annotations

import random

from league_cast_assist.music.models import Song
from league_cast_assist.music.selection import (
    cooldown_remaining,
    freshness_weight,
    group_songs,
    intervening_count,
    pick_weighted,
    repeat_eligible,
    status_text,
    vote_weight,
)


def song(song_id: int, mode: str = "shuffle", group: str | None = None) -> Song:
    return Song(
        song_id=song_id,
        file_path=f"music/{song_id}.mp3",
        title=f"Track {song_id}",
        artist="Artist",
        playback_mode=mode,  # type: ignore[arg-type]
        group_name=group,
    )


SONGS = [song(index) for index in range(1, 11)]


def test_intervening_count_counts_other_songs_since_last_play() -> None:
    history = [1, 2, 3, 4, 5]
    assert intervening_count(1, history) == 4
    assert intervening_count(5, history) == 0
    assert intervening_count(99, history) is None


def test_repeat_requires_five_intervening_songs() -> None:
    # gap counts the OTHER songs played since this one last started.
    assert intervening_count(1, [1, 2, 3, 4]) == 3
    assert repeat_eligible(1, [1, 2, 3, 4]) is False
    assert repeat_eligible(1, [1, 2, 3, 4, 5, 6]) is True
    assert cooldown_remaining(1, [1, 2, 3, 4]) == 2


def test_repeat_blocked_inside_the_cooldown_window() -> None:
    history = [1, 2, 3]
    assert repeat_eligible(1, history) is False
    assert cooldown_remaining(1, history) == 3


def test_unplayed_tracks_are_always_eligible() -> None:
    assert repeat_eligible(42, [1, 2, 3]) is True
    assert cooldown_remaining(42, [1, 2, 3]) == 0


def test_pick_weighted_never_repeats_inside_the_cooldown() -> None:
    history = [1, 2, 3]
    rng = random.Random(1234)
    picks = {
        pick_weighted(SONGS, history, {}, None, rng=rng).song_id
        for _ in range(50)
    }
    assert 1 not in picks
    assert 2 not in picks
    assert 3 not in picks


def test_pick_weighted_returns_none_when_everything_is_on_cooldown() -> None:
    tiny = [song(1), song(2)]
    history = [1, 2]
    assert pick_weighted(tiny, history) is None


def test_pick_weighted_skips_the_pending_track() -> None:
    pool = [song(1), song(2)]
    assert pick_weighted(pool, [], pending_id=1).song_id == 2


def test_manual_tracks_are_never_auto_picked() -> None:
    pool = [song(1, mode="manual"), song(2, mode="disabled")]
    assert pick_weighted(pool, []) is None


def test_vote_and_freshness_weights_match_the_original_curve() -> None:
    assert vote_weight(0) == 1.0
    assert vote_weight(4) == 3.0
    assert freshness_weight(None) == 1.0
    assert freshness_weight(5) == 0.15
    assert freshness_weight(10) == 1.0
    assert freshness_weight(30) == 1.0


def test_status_text_matches_the_producer_wording() -> None:
    assert status_text(song(1), []) == "Shuffle"
    assert status_text(song(1, mode="manual"), []) == "Manual (no cooldown)"
    assert status_text(song(1), [1, 2]) == "Cooldown (4 more)"
    assert status_text(song(1), [1, 2, 3, 4, 5, 6]) == "Repeat ready (5 apart)"


def test_group_songs_filters_by_group_name() -> None:
    pool = [song(1, group="echoes"), song(2, group="worlds_orchestral"), song(3)]
    assert [item.song_id for item in group_songs(pool, "echoes")] == [1]
    assert [item.song_id for item in group_songs(pool, "worlds_orchestral")] == [2]

# ── autoplay after a one-tap track ─────────────────────────────────────────
def test_manual_track_hands_over_when_autoplay_is_on() -> None:
    from league_cast_assist.music.selection import continues_after_manual

    assert continues_after_manual("manual", True) is True


def test_manual_track_stops_when_autoplay_is_off() -> None:
    from league_cast_assist.music.selection import continues_after_manual

    assert continues_after_manual("manual", False) is False


def test_shuffle_rows_are_not_treated_as_manual() -> None:
    from league_cast_assist.music.selection import continues_after_manual

    # Shuffle rows continue through the normal next_weighted() path, so the
    # hand-over rule only ever describes manual tracks.
    assert continues_after_manual("shuffle", True) is False
    assert continues_after_manual("shuffle", False) is False
