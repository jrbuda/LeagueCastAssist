# Playback UX — Action Plan (for approval)

| Field | Value |
|---|---|
| Document Type | Implementation plan (client) |
| Status | **Draft — awaiting approval** |
| Target | `v0.7.0` |
| Related | [`music-integration-plan.md`](music-integration-plan.md) |

Three producer-reported items, all in the music page / playback engine:

1. The volume slider gets loud far too fast.
2. Fade in/out on pause and on track change (default on).
3. Autoplay the shuffled library after a manual track finishes (default on).

---

## 1. Volume slider — perceptual instead of linear

**Why it feels wrong today.** `PlaybackEngine.set_volume()` maps the slider straight to amplitude:
`gain = percent / 100`. Loudness is perceived logarithmically, so a linear amplitude slider is already loud
near the bottom and every extra percent is a big jump. Moving 5 → 15 % triples the amplitude.

**Proposed mapping.** Slider position → decibels → amplitude, with 0 % = silence and 100 % = full scale:

```
gain = 0                         if percent == 0
gain = 10 ** ((percent - 100) * 0.02)   otherwise   # 0→-40 dB, 50→-20 dB, 100→0 dB
```

| Slider | Today (linear) | Proposed (−40 dB span) | Perceived change |
|---|---|---|---|
| 10 % | 0.10 | 0.0016 | nearly inaudible |
| 25 % | 0.25 | 0.010 | quiet background |
| 50 % | 0.50 | 0.100 | comfortable |
| 75 % | 0.75 | 0.316 | present |
| 100 % | 1.00 | 1.000 | unchanged maximum |

* The handle becomes a **gain control**, not a raw amplitude control: the top of the slider still means "as
  loud as the system allows", so nobody loses headroom.
* Implemented as `playback.slider_to_gain(percent) -> float` (pure, unit-tested), used by `set_volume()` and by
  every fade step so the fade curve stays consistent.
* **Defaults and migration**: default `music.volume` moves 65 → 80 (80 % ≈ gain 0.40, similar *perceived*
  loudness to today's 65 % linear 0.65). Existing `settings.json` values are kept as-is, so an upgrading
  producer will find the app a little quieter than before — safer than louder, and one slider move fixes it.
  No automatic remap of stored values (it would make QA numbers meaningless).
* Settings → Music keeps showing a percentage; the `volume` field stays an int 0–100 so nothing else changes.

**Acceptance:** at 50 % the music is comfortable speech-level background, moving 50 → 55 % is a small step,
and 0 % is silent while 100 % is unchanged from today's maximum.

---

## 2. Fade in / out on pause and track change (default on)

Today only *End Break* fades (30 steps ≈ 1.5 s). Pause and Next cut the audio hard, which pops on most USB
interfaces and sounds abrupt on stream.

**Design**

| Action | Behaviour with fades on | With fades off |
|---|---|---|
| Pause | fade out `fade_ms`, then pause | pause immediately |
| Resume | play at 0 gain and fade in `fade_ms` | play immediately |
| Next / Next Weighted | fade out, switch track, start at 0 and fade in | switch immediately |
| End Break | unchanged (existing ~1.5 s fade) | unchanged |
| Volume change while playing | ramp to the new gain over 100 ms (de-click) | jump |

* New settings: `music.fade_enabled: bool = True`, `music.fade_ms: int = 200` (range 80–500, spin box in
  Settings → Music).
* Engine: replace the single-purpose fade timer with `fade_to(target_gain, duration_ms, then=callback)`, so
  pause/next/end-break all share one implementation. Cancelling a fade (e.g. user clicks Next during a fade)
  restarts it from the current gain rather than jumping.
* Manual, disabled while the engine is already fading out for End Break.

**Acceptance:** no audible click at pause/resume/skip on the Focusrite and on a Voicemod virtual output;
fades complete in ~200 ms by default; turning the setting off restores today's instant behaviour exactly.

---

## 3. Autoplay shuffled library after a manual track (default on)

**Today:** manual tracks (`silver_scrapes`, `echoes`, `worlds_orchestral` — 6 of the 29) stop at the end and
clear the now-playing card. That is correct for a "play the Worlds theme and stop" moment, but producers who
hit a one-tap button mid-break then get silence.

**Design**

* New setting `music.autoplay_after_manual: bool = True` (Settings → Music).
* `MusicPage._on_track_finished()`:
  * manual track + setting **on** → `next_weighted()` continues with the shuffled, vote-weighted picker.
  * manual track + setting **off** → today's behaviour (`Special track finished.`).
  * shuffle track → unchanged (`next_weighted()`).
* The weighted picker is unchanged: repeat floor, vote weighting and cooldowns still apply, so "autoplay"
  cannot immediately re-play the special that just finished.
* Status bar says `Special track finished — continuing with the shuffle.` so the behaviour is visible.

**Acceptance:** one-tap Echoes → at end, a shuffled track starts within a beat; with the setting off it stops
and clears as today; no repeat of the same special inside the five-song cooldown.

---

## 4. Settings surface

Settings → Music gains two checkboxes and one spin box, grouped under **Playback**:

```
Playback
  Volume                 [ 80 ] %
  [x] Fade in/out on pause and track change     [ 200 ] ms
  [x] Continue with the shuffled library after a one-tap track
  [x] Show the 500×200 OBS "Now Playing" window
  [x] Show the music strip on the Match page
```

All four booleans keep pydantic defaults, so an existing `settings.json` picks up the new keys without a
migration step (as with every previous addition in this branch).

---

## 5. Tests and QA

| Level | Coverage |
|---|---|
| Unit | `slider_to_gain()` curve (monotonic, 0 → silent, 100 → 1.0, 50 → ≈0.1), fade step maths, `autoplay_after_manual` branch in the finish handler |
| Harness | engine: pause → gain 0 after fade, resume → gain restored; next → new track starts at 0 and ramps; End Break unchanged |
| Manual | volume feel at 30/50/80 %, pause/resume/skip by ear on speakers **and** through the virtual device, one-tap Echoes → shuffle continues, setting off → stops |

QA notes in `E:\LeagueCastAssist-personal\QA-NOTES.txt` get a new block mirroring these.

---

## 6. Effort

| Piece | Estimate |
|---|---|
| Volume curve + tests + default bump | 0.25 d |
| Shared fade implementation + settings + tests | 0.5 d |
| Autoplay-after-manual + setting + tests | 0.25 d |
| Harness/QA verification and docs | 0.25 d |
| **Total** | **≈ 1.25 days** |

---

## 7. Decisions I need

1. **Curve**: dB-linear over a 40 dB span (recommended, table above), or the gentler `gain = (p/100)²`
   (louder in the middle of the range)?
2. **Default volume** 80 % (recommended, ≈ today's perceived loudness) or keep 65 % (noticeably quieter after
   the change)?
3. **Fade length** 200 ms (recommended) or a different default in the 80–500 ms range?
4. Anything else the one-tap tracks should do when they finish — e.g. always stop for `worlds_orchestral`
   regardless of the autoplay setting? (They are used as "play the theme, then talk" moments.)
