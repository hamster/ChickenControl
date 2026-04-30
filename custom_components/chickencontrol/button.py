"""Button entities — Open and Close per door."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
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
    entities = []
    for door in coordinator.doors:
        entities.append(DoorButton(coordinator, entry, door, "open"))
        entities.append(DoorButton(coordinator, entry, door, "close"))
    async_add_entities(entities)


class DoorButton(CoordinatorEntity[ChickenControlCoordinator], ButtonEntity):

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ChickenControlCoordinator,
        entry: ConfigEntry,
        door: str,
        command: str,
    ) -> None:
        super().__init__(coordinator)
        self._door = door
        self._command = command
        self._attr_unique_id = f"{entry.entry_id}_{door}_{command}"
        self._attr_name = f"{door.capitalize()} {command}"
        self._attr_icon = "mdi:door-open" if command == "open" else "mdi:door-closed"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ChickenControl ({entry.data['host']})",
            manufacturer="ChickenControl",
            model="Pi Door Controller",
            sw_version=entry.domain,
        )

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.command(self._door, self._command)
        except DoorBusy as err:
            raise HomeAssistantError(
                f"Door '{self._door}' is already moving"
            ) from err
        except CannotConnect as err:
            raise HomeAssistantError(f"Cannot reach chickenctl: {err}") from err
        await self.coordinator.async_request_refresh()
