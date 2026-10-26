# AGENTS.md — LeagueCastAssist Context & Planning

## What This Repo Is

LeagueCastAssist is a **PySide6 desktop application** for Windows that gives League of Legends tournament casters a low-friction team-vs-team view while spectating custom games directly through the League client. It consolidates champion, player, ability, item, and item-value information into one window. It is read-only: it never automates ready checks, picks, bans, swaps, chat, lobby, or game actions.

Source code lives in this repo; `dist/` is the deployment unit (built by PyInstaller via `build.ps1`).

---

## Read First

- **`DESIGN.md`** — product goal, MVP scope, user experience, data sources (Live Client Data API, LCU, CommunityDragon), architecture, component responsibilities, data model, configuration, packaging, and known risks.
- **`docs/ARCHITECTURE.md`** — the single authoritative source of architectural standards. Read it before planning or implementing anything.
- **`RELEASE_GUIDE.md`** — release process. Note: this file is **gitignored**; treat it as local-only reference.
- **`docs/prd-template.md`** — the PRD template for feature proposals.

---

## Project Layout

```
LeagueCastAssist/
  build.ps1                     Build script (updater.exe + main app, PyInstaller)
  LeagueCastAssist.spec         Tracked PyInstaller spec for the main app
  updater.spec                  PyInstaller spec for the updater
  pyproject.toml                Package metadata, dependencies, ruff/pytest config
  megaphone-icon.ico / .png     App icon (ico is gitignored, regenerate from png)
  DESIGN.md                     Product & architecture design document
  RELEASE_GUIDE.md              Release guide (gitignored, local only)
  docs/                         Planning and architecture documentation

  src/
    league_cast_assist/
      __init__.py               __version__ (semver, source of truth for the app)
      app.py                    Application entry point (main())
      config.py                 Settings model + persistence (settings.json beside exe)
      logging_config.py         Log setup (league-cast-assist.log beside exe)
      models.py                 Pydantic data models (MatchState, TeamState, PlayerState, ...)
      resources.py              Static resource access / app paths
      update.py                 Update logic: check, download, launch updater.exe
      data/
        client_discovery.py     League client + lockfile discovery, connection status
        lcu_client.py           Read-only LCU (Riot local) API client
        live_client.py          Live Client Data API polling (/liveclientdata/*)
        static_data.py          Champion/item static data fetch + cache (assets/)
        asset_resolver.py       Resolve asset paths to local files or remote URLs
        tooltip_formatter.py    Convert Riot tooltip markup to Qt rich text
        match_state.py          MatchStateReducer: merge LCU/live/static data
        simulation.py           Debug simulation (champion setup without live game)
        controller.py           Poll orchestration / app state coordination
        ability_math.py         Ability math helpers
      ui/
        main_window.py          Main window (teams view, detail panel, graph)
        detail_panel.py         Ability/item detail panel
        graph.py                Item-value / objectives / kills graph
        widgets.py              Player cards, hover-to-describe tooltips
        image_loader.py         Async image loading + cache
        workers.py              QThread/QTimer workers for polling and downloads
        onboarding.py           First-launch asset mode choice
        settings_dialog.py      Settings dialog
        debug_dialog.py         Debug simulation dialog
      tools/
        validate_static_data.py Release validation for champion/item rendering
        audit_ability_text.py   Duplicate ability text audit
        audit_item_text.py      Item tooltip candidate audit

    leaguecastassist_updater/
      updater.py                Standalone updater (built to dist/updater.exe)

  tests/                        pytest suite (config, graph, match_state, update, ...)
  .github/workflows/
    ci.yml                      PR/push lint + test gate
    release.yml                 Tag push (vX.Y.Z) → build + publish GitHub release
```

---

## Build & Test

```
python -m pip install -e ".[dev]"            # dev install
python -m pip install -e ".[packaging]"      # + PyInstaller for building
ruff check .                                  # lint (line-length 100, E/F/I/UP/B rules)
python -m pytest                              # test suite (tests/)
.\build.ps1                                   # QA build: dist/updater.exe + dist/LeagueCastAssist.exe
.\build.ps1 -DebugBuild                       # build with console window for troubleshooting
python -m league_cast_assist.tools.validate_static_data   # release-blocking static data checks
```

Validation gates before shipping:
1. `ruff check .` clean.
2. `python -m pytest` green (62 tests covering config, graph, match-state, update, tooltip formatting, item audit).
3. `python -m league_cast_assist.tools.validate_static_data` — exits non-zero for release blockers (empty tooltips, unresolved raw Riot markup, malformed rich text, duplicate/overlapping ability text). Visible `?` values are warnings unless `--strict-placeholders`.

### Running a packaged build while working in this repo

PyInstaller's onefile bootloader extracts the bundle to `%TEMP%\_MEI<pid>` **before** any app code runs, and
the workspace folder carries a hardening ACL (`Everyone: DeleteSubdirectoriesAndFiles DENY`) that makes that
extraction fail when the exe is started from `E:\code_stuff\…`. The result is the bootloader's
`Could not create temporary directory!` dialog, which looks like an app bug but is not.

- Copy `dist\LeagueCastAssist.exe` to a folder outside the workspace (e.g. `E:\LeagueCastAssist-personal`
  or `%USERPROFILE%\Downloads`) before launching it.
- Do **not** "fix" this by adding a portable/onedir build or by changing `--runtime-tmpdir`: the release
  shape is intentionally identical to 0.5.x because auto-update replaces a single exe.
- Background: `docs/plans/packaging-updater-plan.md` §1.1.

---

## Versioning

Format: `MAJOR.MINOR.PATCH` (no `v` prefix in source; `v` prefix on the git tag).

- Bug fix / minor improvement: `0.5.3 → 0.5.4`
- New feature / visible change: `0.5.x → 0.6.0`
- Breaking change / rewrite: `0.x.y → 1.0.0`

Canonical version lives in **two files that must always match**:
- `src/league_cast_assist/__init__.py` → `__version__`
- `pyproject.toml` → `version`

The app updater compares the local `__version__` against the latest GitHub release tag. The GitHub Release title comes from the tag name, not these values.

---

## Release Flow (summary — see RELEASE_GUIDE.md for detail)

```
your machine                          GitHub Actions (windows-latest)
──────────────                        ──────────────────────────────
bump __init__.py + pyproject.toml  →  push tag vX.Y.Z
ruff + pytest                          CI: lint + tests
commit + tag                           Release: build.ps1 → LeagueCastAssist.exe
git push origin main --tags            + .sha256 → gh release create (auto notes)
```

Two workflows fire on every `v*.*.*` tag push: `ci.yml` (lint + tests) and `release.yml` (lint, tests, build, publish with `contents: write`). Both use the built-in `GITHUB_TOKEN`.

---

## Active Work Items

| Item | Status |
|---|---|
| Current release | v0.5.3 — stable. v0.5.4 in flight. |
| Graph timeline improvements | **Merged to main** (v0.5.4 work): game-stall detection pauses item-value sampling, red-team series render dashed, timeline jumps prune stale samples/objective events, hover/legend tag width fixes. |
| Item passive number recovery | **Merged to main** (v0.5.4 work): item tooltip candidate scoring now prefers resolved numbers over zero-filled legacy text and wrapper noise; melee/ranged split (`@XMeleeRangedSplit@`, `@XSplit@`) placeholders resolve to both variants; numeric product parts compute (e.g. `0.4 x 100` → `40`); hash-GUID calcs get a fuzzy data-value fallback; runtime-only values render `?`. Fixed Dead Man's Plate, Thornmail, Voltaic Cyclosword, Iceborn Gauntlet, Serrated Dirk, Silvermere Dawn, Divine Sunderer, Spectre's Cowl, Winged Moonplate, Aether Wisp, Forbidden Idol, Serpent's Fang, and more. Verified across all 868 `items.json` entries (0 leftover placeholders; `audit_item_text` = 0 errors). |
| In-flight v0.5.4 | Objectives graph is debug-only (spectator payloads can omit epic-monster events); work is validating Riot event payloads for a user-visible objectives view. |
| Past work | Client discovery, LCU + Live Client integration, static data + asset caching, tooltip formatter, item-value graph, standalone updater.exe, What's New dialog, settings/hover-to-describe. |

## Known Technical Debt

- **Asset caching edge cases** — `assets/` lives beside the exe; CommunityDragon paths can shift between patches. `asset_resolver.py` owns all URL/path construction; keep it the single place that maps CDragon paths.
- **Riot API field-shape fragility** — LCU and Live Client Data API fields can change between patches (see DESIGN.md risks). Normalize payloads at the boundary in `data/` and keep parser tolerance: a changed field must not crash the poll loop.
- **Tooltip placeholders** — Riot formulas can depend on runtime state (stacks, current targets, champion counters). `tooltip_formatter.py` strips unsupported markup and shows readable fallbacks; some `?` values are expected and are warnings, not release blockers. Item-only runtime values (e.g. `@HasteFromAD@`, `@MaxHealthDifference@`) are replaced with `?` via `item_runtime_placeholder_replacement` and surfaced as WARNs by `audit_item_text`.
- **Exact gold unavailable** — spectator APIs do not expose player gold. Everything gold-related is labeled `Item Value` (from visible inventory). Never introduce a "gold" label.
- **Hard-coded update URLs** — `update.py` / `updater.py` point at the `jrbuda/LeagueCastAssist` GitHub Releases API; not configurable without code changes.
- **Updater assumes exe layout** — `updater.exe` must be bundled into the main exe via `build.ps1 --add-data dist/updater.exe;.`. If a stale `updater.spec` or manual PyInstaller run omits this, updates fail with "Updater executable not found".
- **LCU credentials** — the League Client lockfile contains a local Riot API password. Never log lockfile contents, the password, or full lockfile paths (see SECURITY.md).
