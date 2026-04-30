"""Door status sensor — three states: open, closed, moving (plus unknown)."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DOOR_STATES, ICONS, OPT_SENSOR_PREFIX
from .coordinator import ChickenControlCoordinator

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ChickenControlCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        DoorSensor(coordinator, entry, door) for door in coordinator.doors
    )


class DoorSensor(CoordinatorEntity[ChickenControlCoordinator], SensorEntity):
    """
    Reports door state as one of: open, closed, moving, unknown.

    Priority:
      1. If chickenctl reports "moving"  → moving  (relay is energised)
      2. If a physical sensor is mapped  → use its state (on→open, off→closed)
      3. Otherwise                       → use chickenctl's last-commanded state
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ChickenControlCoordinator,
        entry: ConfigEntry,
        door: str,
    ) -> None:
        super().__init__(coordinator)
        self._door = door
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{door}_status"
        self._attr_name = f"{door.capitalize()} door"
        self._attr_options = DOOR_STATES
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ChickenControl ({entry.data['host']})",
            manufacturer="ChickenControl",
            model="Pi Door Controller",
            sw_version=entry.domain,
        )

    @property
    def _physical_sensor_entity_id(self) -> str | None:
        key = f"{OPT_SENSOR_PREFIX}{self._door}"
        value = self._entry.options.get(key, "")
        return value or None

    @property
    def native_value(self) -> str:
        chickenctl_state: str = (
            self.coordinator.data.get(self._door, "unknown")
            if self.coordinator.data
            else "unknown"
        )

        if chickenctl_state == "moving":
            return "moving"

        physical_id = self._physical_sensor_entity_id
        if physical_id:
            state = self.hass.states.get(physical_id)
            if state is not None and state.state not in ("unavailable", "unknown"):
                # binary_sensor: "on" = open, "off" = closed
                # plain sensor: pass the raw state value through
                if state.state == "on":
                    return "open"
                if state.state == "off":
                    return "closed"
                return state.state  # forward arbitrary sensor values as-is

        return chickenctl_state

    @property
    def icon(self) -> str:
        return ICONS.get(self.native_value, ICONS["unknown"])
