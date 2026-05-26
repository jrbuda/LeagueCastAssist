from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

import httpx

from league_cast_assist.config import AppSettings
from league_cast_assist.data.asset_resolver import AssetResolver
from league_cast_assist.data.client_discovery import ClientDiscovery, LcuConnectionInfo
from league_cast_assist.data.lcu_client import LcuClient
from league_cast_assist.data.live_client import LiveClient
from league_cast_assist.data.match_state import MatchStateReducer
from league_cast_assist.data.static_data import StaticDataService
from league_cast_assist.data.tooltip_formatter import TooltipFormatter
from league_cast_assist.models import MatchState

LOGGER = logging.getLogger(__name__)

StateCallback = Callable[[MatchState], None]
StatusCallback = Callable[[str], None]
PatchUpdateCallback = Callable[[str, str], None]


@dataclass
class PollResult:
    lcu_connected: bool = False
    live_connected: bool = False
    status: str = ""


class AppController:
    """Coordinates Riot data sources and emits normalized match state."""

    def __init__(
        self,
        settings: AppSettings,
        state_callback: StateCallback,
        status_callback: StatusCallback,
        patch_update_callback: PatchUpdateCallback | None = None,
    ) -> None:
        self._settings = settings
        self._state_callback = state_callback
        self._status_callback = status_callback
        self._patch_update_callback = patch_update_callback
        self._client_discovery = ClientDiscovery()
        self._live_client = LiveClient()
        self._asset_resolver = AssetResolver(
            local_assets=settings.assets.mode == "local",
            version=settings.assets.version,
        )
        self._static_data = StaticDataService(
            version=settings.assets.version,
            download_assets=settings.assets.mode == "local",
        )
        self._static_data.set_progress_callback(self._set_loading_progress)
        self._reducer = MatchStateReducer(
            static_data=self._static_data,
            asset_resolver=self._asset_resolver,
            tooltip_formatter=TooltipFormatter(),
            item_value_sample_seconds=settings.polling.item_value_sample_seconds,
        )
        self._lcu_connection: LcuConnectionInfo | None = None
        self._lcu_client: LcuClient | None = None
        self._last_lcu_poll = 0.0
        self._has_live_state = False
        self._running = False

    @property
    def state(self) -> MatchState:
        return self._reducer.state

    async def run(self, wake_event: asyncio.Event | None = None) -> None:
        self._running = True

        await self._initialize_static_data()
        try:
            await self.poll_once()
        except Exception:
            LOGGER.exception("Unexpected error during initial poll")

        while self._running:
            try:
                await self.poll_once()
            except Exception:
                LOGGER.exception("Unexpected error in poll loop; continuing")
            if not self._running:
                break
            await self._wait_for_next_poll(wake_event)

    def stop(self) -> None:
        self._running = False

    async def _wait_for_next_poll(self, wake_event: asyncio.Event | None) -> None:
        if wake_event is None:
            await asyncio.sleep(self._settings.polling.live_client_seconds)
            return

        try:
            await asyncio.wait_for(
                wake_event.wait(),
                timeout=self._settings.polling.live_client_seconds,
            )
        except TimeoutError:
            return
        finally:
            wake_event.clear()

    async def poll_once(self) -> PollResult:
        result = PollResult()

        if self._should_poll_lcu():
            await self._poll_lcu(result)
        await self._poll_live_client(result)

        state = self._apply_overrides(self._reducer.state)
        if self._has_live_state and not result.live_connected:
            state.status = result.status or (
                "Live Client Data API connection lost; showing last known data"
            )
        elif state.source == "none" and not result.lcu_connected and not result.live_connected:
            state.status = result.status or "League client not connected"
        self._state_callback(state)
        self._status_callback(state.status)
        return result

    def _should_poll_lcu(self) -> bool:
        now = monotonic()
        if self._last_lcu_poll <= 0:
            self._last_lcu_poll = now
            return True
        if now - self._last_lcu_poll >= self._settings.polling.lcu_seconds:
            self._last_lcu_poll = now
            return True
        return False

    async def _initialize_static_data(self) -> None:
        self._set_loading(True, "Loading CommunityDragon metadata", 0, 2)
        self._status_callback("Loading CommunityDragon data")
        try:
            version_status = await self._static_data.patch_version_status()
            if (
                version_status.update_available
                and version_status.live_version
                and version_status.cached_version
                and self._patch_update_callback is not None
            ):
                self._patch_update_callback(
                    version_status.live_version,
                    version_status.cached_version,
                )
            await self._static_data.ensure_core_data(version_status)
            self._set_loading(False, "Static data ready", 2, 2)
        except (httpx.HTTPError, OSError, UnicodeDecodeError, ValueError) as exc:
            LOGGER.warning("Static data download failed", exc_info=True)
            self._set_loading(False, "Static data download failed", 0, 2)
            self._status_callback(f"Static data download failed: {exc}")
        finally:
            # Suppress progress callback so per-poll ensure_item_assets calls
            # don't flash the loading bar during normal game polling.
            self._static_data.set_progress_callback(None)

    async def _poll_lcu(self, result: PollResult) -> None:
        client = self._get_lcu_client()
        if client is None:
            result.status = "League client not connected"
            return

        try:
            phase = await client.gameflow_phase()
            result.lcu_connected = True
            self._reducer.apply_lcu_phase(phase)

            lobby = await optional_get(client.lobby)
            self._reducer.apply_lobby(lobby)

            champ_select = await optional_get(client.champ_select_session)
            await self._reducer.apply_champ_select(champ_select)
        except (httpx.HTTPError, OSError, UnicodeDecodeError, ValueError) as exc:
            LOGGER.debug("LCU poll failed", exc_info=True)
            result.status = f"LCU unavailable: {exc.__class__.__name__}"
            self._lcu_client = None
            self._lcu_connection = None

    async def _poll_live_client(self, result: PollResult) -> None:
        try:
            payload = await self._live_client.all_game_data()
        except (httpx.HTTPError, ValueError):
            LOGGER.debug("Live Client Data API unavailable", exc_info=True)
            result.live_connected = False
            if self._has_live_state:
                result.status = "Live Client Data API connection lost; showing last known data"
            return

        if isinstance(payload, dict):
            result.live_connected = True
            self._has_live_state = True
            try:
                await self._reducer.apply_live_client_data(payload)
            except Exception:
                LOGGER.exception("Error applying live client data payload")

    def _get_lcu_client(self) -> LcuClient | None:
        connection = self._client_discovery.read_lcu_connection()
        if connection is None:
            self._lcu_connection = None
            self._lcu_client = None
            return None

        if self._lcu_connection == connection and self._lcu_client is not None:
            return self._lcu_client

        self._lcu_connection = connection
        self._lcu_client = LcuClient(connection)
        return self._lcu_client

    def _apply_overrides(self, state: MatchState) -> MatchState:
        overridden = state.model_copy(deep=True)

        blue_override = self._settings.team_name_overrides.get("blue")
        red_override = self._settings.team_name_overrides.get("red")
        if blue_override:
            overridden.blue_team.display_name = blue_override
        if red_override:
            overridden.red_team.display_name = red_override

        for player in overridden.players:
            override = self._settings.player_name_overrides.get(player.stable_key)
            if override:
                player.display_name = override

        return overridden

    def _set_loading(self, active: bool, message: str, current: int, total: int) -> None:
        state = self._reducer.state
        state.loading_active = active
        state.loading_message = message
        state.loading_current = current
        state.loading_total = total
        self._state_callback(self._apply_overrides(state))

    def _set_loading_progress(self, message: str, current: int, total: int) -> None:
        if not message and total <= 0:
            self._set_loading(False, "", 0, 0)
            return
        self._set_loading(True, message, current, total)


async def optional_get(fetch: Callable[[], Any]) -> Any:
    try:
        return await fetch()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {404, 409}:
            return None
        raise
