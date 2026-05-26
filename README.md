# LeagueCastAssist

Native desktop companion app for League of Legends custom-game casters.

See `DESIGN.md` for the current product and architecture direction.

LeagueCastAssist is not endorsed by Riot Games and does not reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games and League of Legends are trademarks or registered trademarks of Riot Games, Inc.

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

## Updates and Releases

Packaged Windows builds check GitHub Releases for newer versions at startup. Users can also run `File > Check for Updates`. The updater compares the local `league_cast_assist.__version__` value against the latest GitHub release tag, downloads the release exe asset, verifies the SHA-256 checksum when GitHub or the release asset provides one, and swaps the exe after the app exits.

Release process:

1. Update the version in `pyproject.toml` and `src/league_cast_assist/__init__.py`.
2. Run `ruff check .` and `python -m pytest`.
3. Tag the release as `vX.Y.Z` and push the tag.
4. The `Release` GitHub Actions workflow builds `dist/LeagueCastAssist.exe`, publishes it to GitHub Releases, and uploads `LeagueCastAssist.exe.sha256` for updater verification.

The repository is safe to make public without committing generated builds, local settings, logs, cached CommunityDragon assets, update downloads, or environment files; those paths are covered by `.gitignore`.

## Manual Testing

1. Start League of Legends and join/spectate a custom game.
2. Start LeagueCastAssist with `league-cast-assist` or `python -m league_cast_assist.app`.
3. On first launch, choose local asset caching unless you specifically want remote assets.
4. Wait for status to move from `League client not connected` to LCU or live-game connected.
5. During a live spectated game, verify both teams populate with champion/ability/item data.
6. Click abilities/items to inspect formatted details.
7. Click one or more player portraits and use the graph toggle to compare selected-player item value.
8. Use `File > Debug > Simulate Champion Setup...` to pause live polling and populate the UI from selected champion dropdowns for ability validation.
9. Use `File > Debug > Stop Simulation` to resume live Riot API polling.
10. Verify the default `1600x900` window keeps both 5-card team rows visible without team scrolling.

Settings, logs, cached assets, and downloaded update files are stored beside the running executable in `settings.json`, `league-cast-assist.log`, `assets/`, and `updates/`. In source runs, those paths are relative to the current working directory.

## Data Notes

- CS is read from Live Client Data API `allPlayers[].scores.creepScore`. In spectator mode this is the closest official local value currently exposed to the app. It can lag or disagree slightly with the in-game scoreboard during live updates, especially around jungle camps, pets/summons, scoreboard refresh timing, or replay/spectator delay. The unsupported spectator `activePlayer` endpoint does not provide a better direct CS value.
- Exact player gold is not exposed to spectators. Graphs labeled `Item Value` use visible inventory item total values, not live gold.
- `Player Value` graph mode supports selecting multiple player cards or checkboxes to compare visible item value over time.
- Objective graph mode is hidden from normal use because current spectator APIs can omit epic monster events. It can be enabled from `File > Debug > Show Objectives Graph` while testing Riot event payloads.
