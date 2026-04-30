"""Async HTTP client for the chickenctl daemon."""

from __future__ import annotations

import asyncio
import logging

import aiohttp

log = logging.getLogger(__name__)

_TIMEOUT = 10  # seconds


class CannotConnect(Exception):
    """Raised when the daemon is unreachable or returns an unexpected error."""


class InvalidAuth(Exception):
    """Raised when the daemon returns 401."""


class DoorBusy(Exception):
    """Raised when the daemon returns 409 (door already moving)."""


class ChickenCtlClient:
    """Thin async wrapper around the chickenctl HTTPS API."""

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        session: aiohttp.ClientSession,
        verify_ssl: bool = True,
    ) -> None:
        self._base = f"https://{host}:{port}"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._session = session
        # aiohttp ssl parameter: None = verify, False = skip verification
        self._ssl: bool | None = None if verify_ssl else False

    async def _request(self, method: str, path: str) -> dict:
        url = f"{self._base}{path}"
        try:
            async with asyncio.timeout(_TIMEOUT):
                resp = await self._session.request(
                    method,
                    url,
                    headers=self._headers,
                    ssl=self._ssl,
                )
        except TimeoutError as err:
            raise CannotConnect(f"Timeout connecting to {url}") from err
        except aiohttp.ClientError as err:
            raise CannotConnect(str(err)) from err

        if resp.status == 401:
            raise InvalidAuth
        if resp.status == 409:
            raise DoorBusy
        if resp.status not in (200, 202):
            raise CannotConnect(f"Unexpected HTTP {resp.status} from {url}")

        return await resp.json()

    async def get_doors(self) -> list[str]:
        """Return the list of door names configured on the Pi."""
        data = await self._request("GET", "/doors")
        return data["doors"]

    async def get_status(self, door: str) -> str:
        """Return 'open', 'closed', 'moving', or 'unknown'."""
        data = await self._request("GET", f"/door/{door}/status")
        return data["state"]

    async def command(self, door: str, cmd: str) -> None:
        """Send 'open' or 'close' to a door.  Raises DoorBusy if already moving."""
        await self._request("POST", f"/door/{door}/{cmd}")
