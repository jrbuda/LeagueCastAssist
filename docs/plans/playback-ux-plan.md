# Playback UX — Action Plan (v2, updated with your decisions)

| Field | Value |
|---|---|
| Document Type | Implementation plan (client) |
| Status | **Ready for approval** |
| Target | `v0.7.0` |
| Related | [`music-integration-plan.md`](music-integration-plan.md) |

Three producer-reported items plus your answer on the volume curve:

1. Volume slider gets loud far too fast → **ship a curve selector so you can A/B it by ear** (§1).
2. Fade in/out on pause and track change — **default on** (§2).
3. Autoplay the shuffled library after a one-tap track — **default on** (§3).

---

## 1. Volume — testable curves instead of one guess

**Why it feels wrong today.** The slider is linear in *amplitude*: `gain = percent / 100`. Loudness is
perceived logarithmically, so the bottom third of the slider is where all the discomfort lives — 5 % → 15 %
triples the amplitude.

**What ships for QA: four curves, switchable live.** This is deliberately a testable iteration, not a final
answer: pick the one that feels right with your ears, and I will collapse the list to it.

| Curve | Formula | 10 % | 25 % | 50 % | 75 % | 100 % |
|---|---|---|---|---|---|---|
| `linear` (today) | `p/100` | 0.100 | 0.250 | 0.500 | 0.750 | 1.00 |
| `squared` | `(p/100)²` | 0.010 | 0.063 | 0.250 | 0.563 | 1.00 |
| **`db40` (my default)** | `10^((p−100)·0.02)` | 0.016 | 0.032 | 0.100 | 0.316 | 1.00 |
| `db60` | `10^((p−100)·0.03)` | 0.002 | 0.006 | 0.032 | 0.178 | 1.00 |

* `db40` keeps usable resolution across the whole travel and still gives full scale at the top.
* `db60` is even gentler at the bottom — good if you want "whisper" settings, but 75 % becomes quiet.
* `squared` is the compromise that stays loud in the middle if producers expect that.
* **Applies live**: changing the curve re-applies the current slider value immediately, so you can play a
  track, move the slider to a fixed position and flip curves to compare without restarting.

Implementation: `playback.slider_to_gain(percent, curve) -> float`, pure and unit-tested; `set_volume()` and
every fade step use it. Settings: `music.volume_curve: "db40"` (default), `music.volume: 80`.

Note on the default: 80 % on `db40` ≈ 0.40 gain, i.e. about today's perceived loudness at 65 % linear. If you
switch to `linear` during testing, 80 % will feel louder than you are used to — that is the point of the A/B.

**After you pick:** hide the selector behind an "Advanced" disclosure (or drop the other three curves) and set
the chosen one as the default. The setting key stays, so nobody's saved choice breaks.

---

## 2. Fade in / out on pause and track change — default on

Only *End Break* fades today (30 steps ≈ 1.5 s); pause and Next cut hard, which pops on USB interfaces.

| Action | Fades on (default) | Fades off |
|---|---|---|
| Pause | fade out `fade_ms`, then pause | pause now |
| Resume | start at 0 and fade in | play now |
| Next / Next Weighted | fade out → switch → fade in | switch now |
| End Break | unchanged (~1.5 s) | unchanged |
| Volume change while playing | 100 ms ramp (de-click) | jump |

* `music.fade_enabled: bool = True`, `music.fade_ms: int = 200` (80–500, spin box).
* One engine primitive: `fade_to(target_gain, duration_ms, then=…)` replaces the End-Break-only timer; a new
  command during a fade restarts from the current gain instead of jumping.
* Inactive while End Break is already fading out.

**Acceptance:** no click at pause/resume/skip on the Focusrite and through a Voicemod virtual output; default
fade ≈ 200 ms; turning the setting off restores today's instant behaviour exactly.

---

## 3. Autoplay the shuffle after a one-tap track — default on

**Today** manual tracks (Silver Scrapes, Echoes, the four Worlds orchestral) stop and clear the card.

* `music.autoplay_after_manual: bool = True`.
* `_on_track_finished()`: manual + setting on → `next_weighted()`; manual + off → today's
  `Special track finished.`; shuffle tracks unchanged.
* The weighted picker still enforces the five-song floor, so autoplay cannot immediately replay the special.
* Status line: `Special track finished — continuing with the shuffle.`
* Applies to **all** manual tracks, including Worlds orchestral. If you later want Worlds to always stop
  ("play the theme, then talk"), that is a one-line exception per group name — say so and it goes in.

---

## 4. Settings → Music layout

```
Playback
  Volume                          [ 80 ] %
  Volume curve (experimental)     [ dB −40 (recommended) ▾ ]      ← linear / squared / dB−40 / dB−60
  [x] Fade in/out on pause and track change      [ 200 ] ms
  [x] Continue with the shuffled library after a one-tap track
  [x] Show the 500×200 OBS "Now Playing" window
  [x] Show the music strip on the Match page
```

All keys are additive with pydantic defaults, so existing `settings.json` files load unchanged.

---

## 5. Tests and QA

| Level | Coverage |
|---|---|
| Unit | `slider_to_gain` for all four curves (monotonic, 0 → 0.0, 100 → 1.0, expected mid-points), fade step maths, autoplay branch |
| Harness | pause → gain 0 then resume → restored; Next → new track starts at 0 and ramps; End Break unchanged |
| Manual (yours) | with a track playing: set the slider to 25/50/75 % and flip all four curves; pick a favourite; then confirm pause/resume/skip have no clicks and that one-tap Echoes continues into the shuffle |

QA notes in `E:\LeagueCastAssist-personal\QA-NOTES.txt` gain a "Volume curve A/B" block and the two
fade/autoplay checks.

---

## 6. Effort

| Piece | Estimate |
|---|---|
| Curve function + selector + tests | 0.35 d |
| Shared fade primitive + settings + tests | 0.5 d |
| Autoplay-after-manual + setting + tests | 0.25 d |
| Settings/QA/docs updates | 0.25 d |
| **Total** | **≈ 1.35 days** |

---

## 7. Decisions recorded

| # | Question | Outcome |
|---|---|---|
| 1 | Curve | **Ship all four selectable**, default `db40`; collapse after your QA pick |
| 2 | Default volume | **80 %** with `db40` (≈ today's perceived 65 %) |
| 3 | Fade length | **200 ms**, configurable 80–500 |
| 4 | Worlds orchestral | Treat as any other manual track for now (autoplay applies); one-line exception available on request |
