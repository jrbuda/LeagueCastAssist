# LeagueCastAssist

Native desktop companion app for League of Legends custom-game casters.

See `DESIGN.md` for the current product and architecture direction.

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
pytest
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
python -m pip install -e .[packaging]
.\build.ps1
```

Use `.\build.ps1 -DebugBuild` if you need a console window while troubleshooting local Riot API or asset-cache issues.
When `Download assets locally` is enabled, downloaded CommunityDragon data and images are stored in an `assets` folder beside the running executable.

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

Logs are written to the user log directory via `platformdirs`, typically under `%LOCALAPPDATA%\LeagueCastAssist\LeagueCastAssist\Logs` on Windows.

## Data Notes

- CS is read from Live Client Data API `allPlayers[].scores.creepScore`. In spectator mode this is the closest official local value currently exposed to the app. It can lag or disagree slightly with the in-game scoreboard during live updates, especially around jungle camps, pets/summons, scoreboard refresh timing, or replay/spectator delay. The unsupported spectator `activePlayer` endpoint does not provide a better direct CS value.
- Exact player gold is not exposed to spectators. Graphs labeled `Item Value` use visible inventory item total values, not live gold.
- `Player Value` graph mode supports selecting multiple player cards or checkboxes to compare visible item value over time.
- Objective graph mode is hidden from normal use because current spectator APIs can omit epic monster events. It can be enabled from `File > Debug > Show Objectives Graph` while testing Riot event payloads.
