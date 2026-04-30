"""DataUpdateCoordinator — polls chickenctl for door states."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CannotConnect, ChickenCtlClient, InvalidAuth
from .const import DOMAIN, SCAN_INTERVAL_SECONDS

log = logging.getLogger(__name__)


class ChickenControlCoordinator(DataUpdateCoordinator[dict[str, str]]):
    """Polls GET /door/{name}/status for every configured door."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: ChickenCtlClient,
        doors: list[str],
    ) -> None:
        super().__init__(
            hass,
            log,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.client = client
        self.doors = doors

    async def _async_update_data(self) -> dict[str, str]:
        data: dict[str, str] = {}
        try:
            for door in self.doors:
                data[door] = await self.client.get_status(door)
        except InvalidAuth as err:
            raise UpdateFailed("Authentication failed — check the API token") from err
        except CannotConnect as err:
            raise UpdateFailed(f"Cannot reach chickenctl: {err}") from err
        return data
