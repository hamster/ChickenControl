"""Cover entity — gives a door-lock-style tile with open/close buttons."""

from __future__ import annotations

import logging

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
    CoverState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CannotConnect, DoorBusy
from .const import DOMAIN
from .coordinator import ChickenControlCoordinator

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ChickenControlCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ChickenDoorCover(coordinator, entry, door) for door in coordinator.doors
    )


class ChickenDoorCover(CoordinatorEntity[ChickenControlCoordinator], CoverEntity):
    """
    Door cover.  The daemon returns "opening"/"closing" so direction is always
    known without local tracking.  Open cancels an in-progress close; close is
    rejected (409) while opening.
    """

    _attr_has_entity_name = False
    _attr_device_class = CoverDeviceClass.DOOR
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(
        self,
        coordinator: ChickenControlCoordinator,
        entry: ConfigEntry,
        door: str,
    ) -> None:
        super().__init__(coordinator)
        self._door = door
        self._attr_unique_id = f"{entry.entry_id}_{door}_cover"
        self._attr_name = door.capitalize()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ChickenControl ({entry.data['host']})",
            manufacturer="ChickenControl",
            model="Pi Door Controller",
            sw_version=entry.domain,
        )

    @property
    def _daemon_state(self) -> str:
        if self.coordinator.data is None:
            return "unknown"
        return self.coordinator.data.get(self._door, "unknown")

    @property
    def state(self) -> str | None:
        s = self._daemon_state
        return {
            "open": CoverState.OPEN,
            "closed": CoverState.CLOSED,
            "opening": CoverState.OPENING,
            "closing": CoverState.CLOSING,
            "moving": CoverState.OPENING,  # fallback for older daemon
        }.get(s)  # None → unavailable

    @property
    def is_closed(self) -> bool | None:
        s = self._daemon_state
        if s == "closed":
            return True
        if s in ("open", "opening", "closing"):
            return False
        return None

    async def async_open_cover(self, **kwargs) -> None:
        try:
            await self.coordinator.client.command(self._door, "open")
        except DoorBusy as err:
            raise HomeAssistantError(
                f"Cannot open '{self._door}' while it is already opening"
            ) from err
        except CannotConnect as err:
            raise HomeAssistantError(f"Cannot reach chickenctl: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_close_cover(self, **kwargs) -> None:
        try:
            await self.coordinator.client.command(self._door, "close")
        except DoorBusy as err:
            raise HomeAssistantError(
                f"Cannot close '{self._door}' while it is moving"
            ) from err
        except CannotConnect as err:
            raise HomeAssistantError(f"Cannot reach chickenctl: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_stop_cover(self, **kwargs) -> None:
        """While closing, reverse to open (emergency chicken rescue). While opening, no-op."""
        if self._daemon_state == "closing":
            await self.async_open_cover()
