"""ChickenControl integration — manual door control via chickenctl daemon."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ChickenCtlClient
from .const import CONF_DOORS, CONF_TOKEN, CONF_VERIFY_SSL, DOMAIN
from .coordinator import ChickenControlCoordinator

log = logging.getLogger(__name__)

PLATFORMS = ["button", "sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass, verify_ssl=entry.data[CONF_VERIFY_SSL])
    client = ChickenCtlClient(
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        token=entry.data[CONF_TOKEN],
        session=session,
        verify_ssl=entry.data[CONF_VERIFY_SSL],
    )
    coordinator = ChickenControlCoordinator(
        hass, client, doors=entry.data[CONF_DOORS]
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when the user changes options (sensor mappings)."""
    await hass.config_entries.async_reload(entry.entry_id)
