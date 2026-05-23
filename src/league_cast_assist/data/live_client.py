from __future__ import annotations

import httpx


class LiveClient:
    """Client for Riot's local Live Client Data API."""

    BASE_URL = "https://127.0.0.1:2999/liveclientdata"

    async def all_game_data(self) -> dict | None:
        timeout = httpx.Timeout(3.0, connect=0.75)
        async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
            response = await client.get(f"{self.BASE_URL}/allgamedata")
            response.raise_for_status()
            return response.json()

    async def is_available(self) -> bool:
        try:
            await self.all_game_data()
        except (httpx.HTTPError, ValueError):
            return False
        return True
