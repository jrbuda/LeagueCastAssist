# LeagueCastAssist — Architecture & Coding Standards

| Field | Value |
|---|---|
| Document Type | Architecture Standards |
| Repository | LeagueCastAssist |
| Status | Active |
| Last Updated | August 5, 2026 |

---

## 1. Purpose

This document defines architectural standards, coding conventions, and mandatory practices for all work in the LeagueCastAssist repository. All new features, refactors, and bug fixes must conform to these guidelines.

---

## 2. Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| GUI Framework | PySide6 (Qt for Python) 6.7+ |
| HTTP Client | httpx (async) 0.27+ |
| Data Models | pydantic v2 (BaseModel) |
| Process/Threading | QThread + Signal/Slot (ui/workers.py) |
| Packaging | PyInstaller 6+ (single-file EXE) |
| Build Script | `build.ps1` (updater.exe then main app) |
| Update Mechanism | Standalone `updater.exe` (PyInstaller-built, `--windowed`) |
| Static Data | CommunityDragon (preferred) / DataDragon, cached in `assets/` beside exe |
| Lint / Test | ruff (line-length 100, E/F/I/UP/B) / pytest |

---

## 3. Project Architecture

### 3.1 Application Layers

```
src/league_cast_assist/
  entrypoint: app.py               main(), QApplication, global stylesheet, worker orchestration
  models:    models.py             Pydantic models (MatchState, TeamState, PlayerState, ...)
  config:    config.py             AppSettings model + persistence (settings.json)
  data:      data/                 ClientDiscovery, LcuClient, LiveClient, StaticDataService,
                                   AssetResolver, TooltipFormatter, MatchStateReducer,
                                   Simulation, Controller, GameActivityTracker
  music:     music/                MusicLibrary, PlaybackEngine, selection rules, MediaCatalog
  ui:        ui/                   PySide6 views (main_window, match_page, music_page, music_strip,
                                   now_playing_window, detachable_tabs, detail_panel, graph, widgets)
  theme:     ui/theme.py           Single APP_STYLESHEET applied to the QApplication
  tools:     tools/                Release validation & audit CLI scripts
  update:    update.py             Update check / download / launch updater.exe
  logging:   logging_config.py     Log setup (league-cast-assist.log beside exe)

src/leaguecastassist_updater/
  updater.py                       Standalone updater (built to dist/updater.exe)
```

### 3.2 Key Data Flow

```
User launches app
  → app.py loads AppSettings from settings.json (beside exe; cwd in dev)
  → app.py applies ui/theme.py to the QApplication (so detached windows stay themed)
  → ClientDiscovery finds League lockfile, reports connection status
  → DataWorker (QThread) polls LCU (read-only) + Live Client Data API
  → LiveClient reads /liveclientdata/allgamedata
  → MatchStateReducer merges LCU + live + static data into MatchState
  → MainWindow renders team-vs-team cards; detail_panel shows ability/item info
  → graph.py samples item value every N seconds (in-memory, current match only)

Game-state driven page focus
  → MainWindow feeds each MatchState to GameActivityTracker
  → tracker emits GAME_STARTED / GAME_ENDED (with a configurable grace period)
  → MainWindow switches to the Match page (in game) or the Music page (idle),
    raising the window only when the corresponding setting is enabled

Music
  → MusicLibrary opens/seeds music.db (WAL) and exposes songs, history, votes
  → PlaybackEngine (two QMediaPlayer outputs) plays from assets/media
  → MusicPage renders now-playing, transport, library; MusicStrip mirrors it in-match
  → NowPlayingWindow exposes the fixed 500x200 OBS capture surface
  → MediaCatalog (QThread worker) syncs assets/media from the central media server

User selects an ability/item
  → TooltipFormatter converts Riot markup to Qt rich text
  → DetailPanel shows formatted tooltip, cooldown, cost, range, scaling

Update flow
  → update.py checks GitHub Releases for a newer __version__
  → downloads LeagueCastAssist-vX.Y.Z.exe + verifies SHA-256
  → launches updater.exe detached; app quits; updater swaps exe and relaunches
```

### 3.3 Process Model

| Process | Executable | Entry Point | Console |
|---|---|---|---|
| Main App | `LeagueCastAssist.exe` | `src/league_cast_assist/app.py` | Windowed (no console) |
| Updater | `updater.exe` | `src/leaguecastassist_updater/updater.py` | Windowed (logs to updater.log) |

The main exe bundles `updater.exe` via `build.ps1 --add-data dist/updater.exe;.`. At install time `update.py` copies it from `_MEIPASS` to the app dir (always overwriting to keep it fresh).

---

## 4. Coding Standards

### 4.1 Python Style

- Follow **PEP 8** with these project-specific conventions:
  - Line length: 100 characters (per `pyproject.toml` `[tool.ruff] line-length = 100`)
  - Indentation: 4 spaces (no tabs)
  - Class names: `PascalCase` (`MatchStateReducer`, `StaticDataService`)
  - Method/variable names: `snake_case` (`poll_all_game_data`, `_player_side_lookup`)
  - Constants: `UPPER_SNAKE_CASE` (`APP_NAME`, `APP_AUTHOR`)
  - Private members: prefix with `_` (`_last_sample_monotonic`, `_ability_cache`)
- Use **type hints** on all function signatures and key data structures
- Prefer **pydantic `BaseModel`** for data containers (see `models.py`, `config.py`)
- Use `LOGGER = logging.getLogger(__name__)` per module; use the logger from `logging_config.py` for app-wide setup

### 4.2 Imports Order

1. Standard library
2. Third-party libraries (PySide6, httpx, pydantic, etc.)
3. Application modules (`league_cast_assist.data`, `league_cast_assist.ui`, etc.)

Use absolute imports within the `league_cast_assist` package.

### 4.3 HTTP & Riot Integration

- Use **httpx** (async) for all HTTP. Prefer `httpx.AsyncClient` with explicit timeouts (`httpx.Timeout(...)`).
- `verify=False` is allowed **only** for local Riot HTTPS endpoints (LCU/Live Client on 127.0.0.1). Never disable verification for remote endpoints.
- LCU auth uses `httpx.BasicAuth("riot", <lockfile password>)`. The lockfile password is a local secret — never log it, never commit it, never write it to disk beyond the in-memory connection object.
- Normalize Riot payloads at the boundary in `data/`. A changed/missing field must never crash the poll loop — use tolerant parsing and typed fallbacks.
- All asset URL/path construction lives in `asset_resolver.py`. Keep CommunityDragon path mapping in that single place.

### 4.4 Threading & UI

- Background work uses `QThread` + `Signal`/`Slot` (see `ui/workers.py`: `DataWorker`, `StaticDataDownloadWorker`, `UpdateCheckWorker`, `CatalogDownloadWorker`, etc.). Images use `QThreadPool` + `QRunnable` (see `ui/image_loader.py`).
- Never call Qt widget methods from non-main threads — emit a Signal and handle it on the main thread.
- Poll loops must be resilient: catch `httpx.HTTPError`, `OSError`, `ValueError` at loop boundaries and keep going.
- Styling lives in `ui/theme.py` and is applied with `QApplication.setStyleSheet()`, never on a single window. Pages can be torn off into their own window (`ui/detachable_tabs.py`); a window-level stylesheet would be lost on re-parenting.
- Non-visual state that must survive a tear-off (playback engine, library, current track) is owned by the shell and injected into the page. Never create a second engine per window.
- Audio belongs to the GUI thread (`QMediaPlayer`/`QAudioOutput`). Only file/network work goes to workers.

### 4.5 Data & Configuration

- User config: `settings.json` beside the executable (frozen) or cwd (dev). Written atomically: write to `*.tmp` then `temp_path.replace(path)` (see `config.py:save_settings`).
- Cached static data / images: `assets/` beside the executable.
- Music media: `assets/media/music/` and `assets/media/artwork/`, referenced by `music.db` rows as `music/...` and `artwork/...` (media-root relative). Never bundle audio with the exe.
- Music library: `music.db` beside the executable, seeded from the packaged `music/seed/music.db` on first run. Open with WAL + `busy_timeout` so the external Twitch vote writer never blocks playback.
- Logs: `league-cast-assist.log` (app) and `updater.log` (updater) beside the executable.
- Exact gold is unavailable from spectator APIs — graphs must be labeled `Item Value`, never `Gold`.
- Item-value samples are in-memory for the current match only; trim to a bounded window (see `match_state.py`).
- New config sections must be additive with pydantic defaults so an existing `settings.json` keeps working without migration code.

### 4.6 Error Handling

- Use specific exception types. Avoid bare `except:` — use `except Exception:` minimally.
- Catch and log expected boundary failures (network, file, parse) with `exc_info=True` where helpful.
- Validate external inputs (Riot payloads, release metadata, config JSON) immediately at the boundary.

---

## 5. Build & Distribution

See `RELEASE_GUIDE.md` for full details. Key points:
- `build.ps1` runs two PyInstaller steps: `updater.exe` (onefile, windowed) then `LeagueCastAssist.exe` (onefile, windowed, bundles updater.exe).
- `LeagueCastAssist.spec` is tracked and must stay in sync with `build.ps1` datas (`--add-data dist/updater.exe;.`, `megaphone-icon.ico`, `megaphone-icon.png`).
- `megaphone-icon.ico` is **gitignored** — regenerate from `megaphone-icon.png` via Pillow before building.
- Version is `MAJOR.MINOR.PATCH` in two places that must match: `src/league_cast_assist/__init__.py` (`__version__`) and `pyproject.toml` (`version`).
- Release happens via git tag `vX.Y.Z`; GitHub Actions builds and publishes. No local build is required for release.

---

## 6. Code Review Checklist

### CRITICAL (each finding blocks release)

- [ ] No hard-coded credentials, API keys, or tokens in committed code
- [ ] LCU lockfile password / full lockfile contents never logged or committed
- [ ] `verify=False` used only for local Riot HTTPS endpoints (127.0.0.1), never remote
- [ ] No `print()` statements in production code (tools/* CLI scripts and updater may print intentionally)
- [ ] Missing error handling: no try/except around httpx/network calls, file I/O, or subprocess launches
- [ ] Breaking backward compatibility: removed/renamed keys in `settings.json` without a migration or backfill
- [ ] Thread safety: no Qt widget method call from a non-main thread without Signal/Slot
- [ ] Any new `--add-data`/resource not added to `build.ps1` and `LeagueCastAssist.spec` datas lists
- [ ] New page/view styles declared on a specific window instead of `ui/theme.py`, or non-visual state owned by a detachable page (breaks tear-off)

### WARNING (should fix before release)

- [ ] Missing type hints on any new or modified function signature
- [ ] Uses `requests` instead of httpx, or a plain dict where a pydantic model is expected
- [ ] JSON writes not using the atomic temp-then-replace pattern (`config.py:save_settings`)
- [ ] Import order violation (stdlib → third-party → app)
- [ ] Bare `except:` instead of `except Exception:`
- [ ] Swallowed exceptions: except blocks that silently pass/return without logging
- [ ] New asset URL/path construction outside `asset_resolver.py`
- [ ] Duplicated logic between modules that should share a helper
- [ ] New config sections missing pydantic defaults (a v0.5.x `settings.json` must still load)
- [ ] Missing or misleading logging for significant operations
- [ ] Poll-loop changes that can crash on unexpected Riot field shapes

### SUGGESTION (nice to have)

- [ ] Dead code: commented-out blocks, unreachable code, unused imports
- [ ] Private members missing `_` prefix
- [ ] Data containers not using pydantic `BaseModel`
- [ ] Missing PyInstaller hiddenimports for new imports
- [ ] Unfriendly error messages shown to users
- [ ] Performance issues: repeated network calls without caching, unbounded caches
- [ ] Resource leaks: unclosed httpx clients, unbounded image/asset caches

---

## 7. Dependencies

| Package | Min Version | Required By |
|---|---|---|
| PySide6 | 6.7 | GUI |
| httpx | 0.27 | HTTP (LCU, Live Client, static data, updates) |
| pydantic | 2.7 | Data models / config |
| psutil | 5.9 | Updater process wait |
| PyInstaller | 6.8 | Build (packaging extra, dev only) |
| pytest | 8.2 | Tests (dev extra) |
| ruff | 0.5 | Lint (dev extra) |

---

## 8. References

| Resource | Location |
|---|---|
| Product / design | `DESIGN.md` |
| Release process | `RELEASE_GUIDE.md` (gitignored) |
| Build script | `build.ps1` |
| Main app spec | `LeagueCastAssist.spec` |
| Updater spec | `updater.spec` |
| Updater source | `src/leaguecastassist_updater/updater.py` |
| Planning docs | `docs/` |
| Music integration plan | `docs/plans/music-integration-plan.md` |
| Media manifest spec | `docs/plans/media-manifest-spec.md` |
| CI | `.github/workflows/ci.yml` |
| Release workflow | `.github/workflows/release.yml` |
