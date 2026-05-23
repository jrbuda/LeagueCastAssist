from __future__ import annotations

import httpx

from league_cast_assist.data.client_discovery import LcuConnectionInfo


class LcuClient:
    """Read-only client for League Client Update endpoints."""

    def __init__(self, connection: LcuConnectionInfo) -> None:
        self._connection = connection
        self._base_url = f"{connection.protocol}://127.0.0.1:{connection.port}"

    async def get(self, path: str) -> dict | list | str | int | float | bool | None:
        timeout = httpx.Timeout(3.0, connect=0.75)
        async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
            response = await client.get(
                f"{self._base_url}{path}",
                auth=httpx.BasicAuth("riot", self._connection.password),
            )
            response.raise_for_status()
            if not response.content:
                return None
            return response.json()

    async def is_available(self) -> bool:
        try:
            await self.gameflow_phase()
        except (httpx.HTTPError, ValueError):
            return False
        return True

    async def gameflow_phase(self) -> str | None:
        data = await self.get("/lol-gameflow/v1/gameflow-phase")
        return data if isinstance(data, str) else None

    async def gameflow_session(self) -> dict | None:
        data = await self.get("/lol-gameflow/v1/session")
        return data if isinstance(data, dict) else None

    async def champ_select_session(self) -> dict | None:
        data = await self.get("/lol-champ-select/v1/session")
        return data if isinstance(data, dict) else None

    async def lobby(self) -> dict | None:
        data = await self.get("/lol-lobby/v2/lobby")
        return data if isinstance(data, dict) else None

    async def lobby_members(self) -> list[dict] | None:
        data = await self.get("/lol-lobby/v2/lobby/members")
        return data if isinstance(data, list) else None
