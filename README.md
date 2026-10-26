# LeagueCastAssist

Native desktop companion app for League of Legends custom-game casters.

The window has two pages. **Match** is the team-vs-team champion, ability, item and item-value view.
**Music** is the cast break-music producer: weighted no-repeat playback, an OBS "Now Playing" window and a
one-tap library. Pages can be torn off into their own window by dragging the tab, and the app follows the
game automatically: match data in game, music when idle.

See `DESIGN.md` for the current product and architecture direction.

LeagueCastAssist is not endorsed by Riot Games and does not reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games and League of Legends are trademarks or registered trademarks of Riot Games, Inc.

## Music library

Audio is never bundled with the app. Tracks and artwork live in the media folder beside the app:

```
assets/media/music/<track>.mp3      (or .wav)
assets/media/artwork/<album>.jpg
music.db                            seeded on first run; holds songs, sessions, play history, votes
```

- `Settings → Music` shows the media folder; `Settings → Media Server` will point at the central library
  once it exists (the client-side sync is implemented and currently inert).
- Playback follows the existing producer rules: vote-weighted shuffle with a hard floor of five intervening
  songs, manual-only tracks for specials, and a fade-out for End Break.
- **Stream only** (button on the Music page; the same switch is in Settings → Music) is the single
  audio-routing control. **On**, the music goes only to a virtual audio device that OBS captures and you hear
  nothing; **Off**, it also plays through your default output while still reaching OBS. The output list
  marks virtual endpoints (VB-Cable, Voicemod, NVIDIA Broadcast, Steam Streaming) and the app selects one
  automatically. In OBS add **Audio Output Capture** and pick the matching device; that is a one-time scene
  setup.
  When no virtual device exists, a **Get a virtual device ›** link appears next to the button: it explains
  the requirement, opens the free VB-Cable page, and has a **Re-check** button that re-scans without
  restarting the app. Peripherals plugged in later are detected automatically.
- **OBS "Now Playing" window** — a 500×200 capture surface that exists for the whole app session, even when
  no track is playing (it shows "Waiting for music"). It has no close button and ignores close attempts, so
  an OBS Window Capture source keeps its target; hiding it from Settings asks first, and re-enabling reuses
  the same window title so OBS reconnects on its own.
- `Settings → Focus` controls the automatic page switching (show/raise per direction, grace period, and an
  optional "start music N seconds after a game ends").

## Development

Requirements:

- Python 3.11+
- Windows for League Client integration testing

Create a virtual environment and install the project:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .[dev]
```

Run the app:

```powershell
league-cast-assist
```

Run tests:

```powershell
ruff check .
python -m pytest
```

Validate cached static-data rendering before a release:

```powershell
python -m league_cast_assist.tools.validate_static_data
```

The validator scans all champions, all abilities, and currently filtered Summoner's Rift item data. It exits non-zero for release-blocking errors such as empty tooltips, unresolved raw Riot markup, malformed rich text, or duplicate/overlapping ability text inside a champion kit. Visible `?` values are warnings by default because some Riot formulas depend on runtime stacks or current game state. Use `--strict-placeholders` to make those warnings fail the command while working down the backlog.

Audit rendered champion ability text for exact and near-duplicate skills:

```powershell
python -m league_cast_assist.tools.audit_ability_text
```

Audit selected item descriptions against available Riot item tooltip candidates:

```powershell
python -m league_cast_assist.tools.audit_item_text
```

Build a single-file Windows executable:

```powershell
python -m pip install -e .[dev,packaging]
.\build.ps1
```

Use `.\build.ps1 -DebugBuild` if you need a console window while troubleshooting local Riot API or asset-cache issues.
When `Download assets locally` is enabled, downloaded CommunityDragon data and images are stored in an `assets` folder beside the running executable.

## Troubleshooting

### "Could not create temporary directory!" when starting the exe

`LeagueCastAssist.exe` is a single-file (PyInstaller *onefile*) build: before any app code runs, the
bootloader unpacks Python, Qt and the FFmpeg backend into `%TEMP%\_MEI<pid>`. If that folder cannot be
created — `%TEMP%` missing, on a full disk, redirected by policy, or blocked by security software — the
bootloader shows this dialog and nothing in the app can catch it, because Python has not started yet.

Try, in order:

1. Run the exe from a normal folder on the system drive (e.g. `%USERPROFILE%\Downloads`), not from a
   network path or a secondary drive.
2. Point the temp variables at a writable folder for one launch:
   `set TMP=%LOCALAPPDATA%\LcaTemp && set TEMP=%LOCALAPPDATA%\LcaTemp && LeagueCastAssist.exe`
3. Add the app folder (or `LeagueCastAssist.exe`) to your antivirus exclusions, and clear stale
   `%TEMP%\_MEI*` folders left behind by killed runs.

## Updates and Releases

Packaged Windows builds check GitHub Releases for newer versions at startup. Users can also run `File > Check for Updates`. The updater compares the local `league_cast_assist.__version__` value against the latest GitHub release tag, downloads the release exe asset, verifies the SHA-256 checksum when GitHub or the release asset provides one, and swaps the exe after the app exits.

Release process:

1. Update the version in `pyproject.toml` and `src/league_cast_assist/__init__.py`.
2. Run `ruff check .` and `python -m pytest`.
3. Tag the release as `vX.Y.Z` and push the tag.
4. The `Release` GitHub Actions workflow builds `dist/LeagueCastAssist.exe`, publishes it to GitHub Releases, and uploads `LeagueCastAssist.exe.sha256` for updater verification.

## Data Notes

- CS is read from Live Client Data API `allPlayers[].scores.creepScore`. In spectator mode this is the closest official local value currently exposed to the app. It can lag or disagree slightly with the in-game scoreboard during live updates, especially around jungle camps, pets/summons, scoreboard refresh timing, or replay/spectator delay. The unsupported spectator `activePlayer` endpoint does not provide a better direct CS value.
- Exact player gold is not exposed to spectators. Graphs labeled `Item Value` use visible inventory item total values, not live gold.
- `Player Value` graph mode supports selecting multiple player cards or checkboxes to compare visible item value over time.
- Objective graph mode is hidden from normal use because current spectator APIs can omit epic monster events. It can be enabled from `File > Debug > Show Objectives Graph` while testing Riot event payloads.
