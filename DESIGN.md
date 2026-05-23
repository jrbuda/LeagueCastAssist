# LeagueCastAssist Design Document

## Product Goal

LeagueCastAssist is a native Windows desktop companion app for League of Legends tournament casters who spectate custom games directly through the League client. The app consolidates champion, player, ability, item, and item-value information into a low-friction team-vs-team view so casters can reference information quickly without opening external websites or tools.

## Core Tenets

- Minimal usage friction: launch the app, spectate a custom game, and data appears automatically.
- Custom games only for the first release.
- Native desktop UI; no OBS/browser overlay requirement.
- Read-only integration with Riot local APIs. Do not automate ready checks, picks, bans, swaps, chat, lobby actions, or game actions.
- Data-driven implementation. Champion, ability, item, icon, and tooltip data should come from DataDragon or CommunityDragon, not hardcoded app data.
- Offline-friendly by default through local asset caching, with a first-launch option to use remote assets directly.

## MVP Scope

The MVP should provide:

- League Client detection through the local lockfile.
- Spectated custom-game detection.
- Automatic player, champion, and team population from LCU/live-client data where available.
- Team-vs-team display with both teams visible at once.
- Player cards showing player name, champion name, champion portrait, ability icons, ability names, and compact item icons.
- Ability click behavior that opens an in-app detail panel formatted close to in-game tooltips.
- Item hover behavior for quick item name/cost information.
- Item click behavior that opens a full item detail panel.
- Item-value graph with toggles for team totals, selected-player comparison, and kills.
- Manual fallback for team/player display names when automatic data is missing or wrong.
- Standard dark-mode visual style.

Draft visualization is explicitly out of scope for MVP because the group already has an external draft tool.

## User Experience

### Main Window

The main window should use a clear team-vs-team layout:

Default window size is `1600x900`. The team rows are intentionally compact so both teams remain visible without team scrolling at that size.

```text
Blue Team                                    Red Team

Player Name                                  Player Name
Champion Name + Portrait                     Champion Name + Portrait
P: Passive Name                              P: Passive Name
Q: Ability Name                              Q: Ability Name
W: Ability Name                              W: Ability Name
E: Ability Name                              E: Ability Name
R: Ultimate Name                             R: Ultimate Name
[ item icons row ]                           [ item icons row ]

... repeated for 5 players per side ...

Right side:
Ability/item detail panel and match graph controls.
Item Value graph: team totals / multi-player comparison / kills
```

### Player Cards

Each player card should show:

- Player display name.
- Champion name.
- Champion portrait.
- Passive, Q, W, E, and R icons.
- Ability names directly visible next to the icons.
- A compact item icon row.
- Level, KDA, CS, visible inventory item value, and optional later additions such as summoner spells and runes.

Ability names are intentionally first-class UI content because they are harder for casters to remember than item icons.

### Ability Details

Clicking an ability opens an in-app detail panel with:

- Ability icon.
- Ability name.
- Slot: Passive, Q, W, E, or R.
- Tooltip text formatted similarly to League's in-game display.
- Cooldown, cost, range, and scaling details where available.

Riot tooltip data often contains custom tags and placeholders. The app should convert these into readable rich text instead of showing raw markup.

Formatting goals:

- Convert meaningful tags such as `magicDamage`, `physicalDamage`, `trueDamage`, `scaleAP`, `scaleAD`, `status`, `shield`, and `speed` into readable styled spans.
- Strip unsupported internal tags without losing their text content.
- Avoid showing unresolved placeholders such as `@TotalDamage@` when they cannot be resolved cleanly.
- Prefer caster-readable text over exact raw internal text.

### Item Details

Item icons remain compact in the player card.

- Hovering an item shows a quick tooltip with item name and cost when available.
- Clicking an item opens the full detail panel with icon, name, formatted description, stats, cost, and build information where available.

### Item-Value Graph

Exact gold is not expected from the release/live client APIs, so MVP uses item value instead.

Requirements:

- Track each player's visible inventory value over game time.
- Calculate team totals from player item values.
- Graph blue team total vs red team total.
- Toggle to compare one or more selected players' item value over time.
- Label all graph UI as `Item Value`, not `Gold`.
- Sample every 5-10 seconds for MVP.
- Store samples in memory for the current match only.

### Debug Simulation

A hidden debug workflow is available under `File > Debug` for validation and production hardening:

- `Simulate Champion Setup...` opens champion dropdowns for all 10 player slots.
- Starting simulation pauses live Riot polling and builds a `MatchState` from static champion data.
- Simulated players use the normal team/player cards, ability detail panel, image loading, and graph UI.
- `Stop Simulation` resumes live polling.

This exists to quickly validate champion ability formatting without needing a custom game with specific champions.

## Data Sources

### Live Client Data API

During an active spectated game, use the local Live Client Data API:

- `GET https://127.0.0.1:2999/liveclientdata/allgamedata`
- `GET https://127.0.0.1:2999/liveclientdata/playerlist`
- `GET https://127.0.0.1:2999/liveclientdata/eventdata`
- `GET https://127.0.0.1:2999/liveclientdata/gamestats`
- `GET https://127.0.0.1:2999/liveclientdata/playeritems?summonerName={name}`
- `GET https://127.0.0.1:2999/liveclientdata/playerscores?summonerName={name}`

Primary MVP endpoint: `/liveclientdata/allgamedata`.

### LCU API

Before and around the game, use read-only LCU endpoints through lockfile auth:

- `GET /lol-gameflow/v1/gameflow-phase`
- `GET /lol-gameflow/v1/session`
- `GET /lol-lobby/v2/lobby`
- `GET /lol-lobby/v2/lobby/members`
- `GET /lol-champ-select/v1/session`
- `GET /lol-champ-select/v1/session/timer`
- `GET /lol-summoner/v1/current-summoner`

The LCU WebSocket can be used later for event-driven updates. MVP can start with conservative polling.

### Static Data

CommunityDragon should be the preferred static-data source because it exposes rich client-style champion data and asset paths. DataDragon can be used as a fallback when simpler stable data is sufficient.

Useful CommunityDragon paths:

- Champion summary: `https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/champion-summary.json`
- Champion detail: `https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/champions/{championId}.json`
- Items: `https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/items.json`

Asset-path rule:

- `/lol-game-data/assets/<path>` maps to `https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/<lowercased-path>`.

## Asset Storage

On first launch, show a choice:

- Download assets locally, recommended default.
- Use internet assets directly.

Local download mode:

- Best runtime performance.
- More reliable during streams.
- Requires disk space.
- Stores data under the user's app data directory.
- Should include a later cache clear/update option.

Internet mode:

- Uses less disk space.
- Requires network access while using the app.
- Can be slower or less reliable during streams.

## Architecture

```text
LeagueCastAssist.exe
  App Shell
    PySide6 native desktop window
    Status bar for League connection and game state

  Data Layer
    ClientDiscovery
    LcuClient
    LiveClient
    StaticDataService
    AssetResolver
    TooltipFormatter
    MatchStateReducer

  Views
    Team-vs-team match overview
    Ability/item detail panel
    Item-value graph
    Settings/first-launch dialog
    Debug simulation dialog
```

## Component Responsibilities

### ClientDiscovery

- Locate and parse the League Client lockfile.
- Detect whether LCU is reachable.
- Detect whether Live Client Data API is reachable.
- Report user-facing connection status.

### LcuClient

- Perform read-only LCU requests.
- Disable certificate verification for local Riot HTTPS endpoints only.
- Normalize failures into typed errors.
- Never call mutating endpoints.

### LiveClient

- Poll `/liveclientdata/allgamedata` during active games.
- Extract players, champions, items, scores, events, and game time.
- Handle temporary endpoint unavailability during loading/reconnect transitions.

CS source:

- CS is read from `allPlayers[].scores.creepScore`.
- Spectator `activePlayer` is unsupported and does not expose a better direct CS value.
- Small mismatches against the in-game scoreboard are expected because the Live Client Data API updates on its own cadence and spectator data can lag or aggregate camps/summons differently.

Objective source:

- Objective timeline events are derived from `/liveclientdata/eventdata` and the `events.Events` block in `/allgamedata`.
- Current spectator payloads can emit turret events while omitting dragon, baron, herald, voidgrub, Atakhan, or inhibitor events even after those objectives occur in-game.
- LCU spectate endpoints expose launch/config metadata, and match-history/replay timelines are unavailable during live spectate. Observer REST exposes encrypted chunk/keyframe data but no stable objective summary API.
- The objective graph remains a debug-only view until Riot exposes reliable live objective events locally. The app should not invent exact objective timing when the spectator payload does not include it.

### StaticDataService

- Download and cache champion summary, champion details, items, and asset metadata.
- Store downloaded local assets beside the running executable under `assets/`.
- Provide lookup by champion ID, champion alias/name, and item ID.
- Support local download mode and remote asset mode.
- Provide release validation for all champion ability text and Summoner's Rift item rendering.

### AssetResolver

- Resolve CommunityDragon `/lol-game-data/assets/...` paths to local files or remote URLs.
- Keep asset URL/path construction in one place.

### TooltipFormatter

- Convert Riot/CommunityDragon HTML-like tooltip markup into Qt-renderable rich text.
- Strip unsupported tags safely.
- Provide readable fallbacks when placeholders cannot be resolved.

### MatchStateReducer

- Merge LCU, live-client, and static data into a stable `MatchState` model.
- Preserve last-known good data when one source temporarily fails.
- Build graph samples from visible item inventories and static item prices.

## Data Model Sketch

```python
class MatchState:
    phase: str
    game_time_seconds: float | None
    blue_team: TeamState
    red_team: TeamState
    item_value_samples: list[ItemValueSample]

class TeamState:
    side: Literal["blue", "red"]
    display_name: str
    players: list[PlayerState]

class PlayerState:
    display_name: str
    champion_id: int | None
    champion_name: str | None
    champion_icon: str | None
    abilities: list[AbilityState]
    items: list[ItemState]
    item_value: int

class AbilityState:
    slot: Literal["P", "Q", "W", "E", "R"]
    name: str
    icon: str | None
    short_description: str | None
    full_description: str | None
    cooldown: str | None
    cost: str | None
    range: str | None

class ItemState:
    item_id: int
    name: str
    icon: str | None
    description: str | None
    total_cost: int | None

class ItemValueSample:
    game_time_seconds: float
    blue_total: int
    red_total: int
    player_values: dict[str, int]
```

## Configuration

Config should be stored in the user config directory, not beside the exe.

Initial settings:

```json
{
  "assets": {
    "mode": "local",
    "source": "communitydragon",
    "version": "latest"
  },
  "polling": {
    "live_client_seconds": 2,
    "lcu_seconds": 3,
    "item_value_sample_seconds": 5
  },
  "ui": {
    "theme": "dark"
  }
}
```

## Packaging

Use PyInstaller for a Windows executable.

Packaging requirements:

- Bundle Python app code and Qt dependencies.
- Keep static cache/config/logs in app data outside the executable.
- Provide a console-free app build for normal distribution.
- Provide a debug build or log file for troubleshooting.

## Risks

- LCU endpoints can change between patches.
- Live Client Data API availability and field shape should be validated while spectating custom games.
- Exact team/player gold is unavailable, so item value must be clearly labeled.
- Riot tooltip markup can include unresolved placeholders that need graceful formatting.
- Some Riot formulas depend on runtime-only state such as stacks, current targets, or champion-specific counters. Static validation treats visible placeholder fallbacks as warnings unless strict mode is requested.
- PyInstaller plus PySide6 requires packaging validation early.

## First Technical Spike

Validate `/liveclientdata/allgamedata` during a spectated custom game and capture sample payloads for:

- Player names.
- Team assignment.
- Champion names/IDs or aliases.
- Item IDs.
- Game time.
- Score fields.
- Event data.

The result of this spike determines how much pre-game LCU data must be retained and merged into in-game state.
