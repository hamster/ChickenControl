"""Config flow and options flow for ChickenControl."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CannotConnect, ChickenCtlClient, InvalidAuth
from .const import (
    CONF_DOORS,
    CONF_TOKEN,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    OPT_SENSOR_PREFIX,
)

log = logging.getLogger(__name__)

_STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        vol.Required(CONF_PORT, default=DEFAULT_PORT): NumberSelector(
            NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
        ),
        vol.Required(CONF_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): BooleanSelector(),
    }
)


class ChickenControlConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup: connection details + door discovery."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = int(user_input[CONF_PORT])
            token = user_input[CONF_TOKEN]
            verify_ssl = user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
            client = ChickenCtlClient(host, port, token, session, verify_ssl)

            try:
                doors = await client.get_doors()
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                log.exception("Unexpected error during setup")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"ChickenControl ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_TOKEN: token,
                        CONF_VERIFY_SSL: verify_ssl,
                        CONF_DOORS: doors,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_STEP_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ChickenControlOptionsFlow(config_entry)


class ChickenControlOptionsFlow(OptionsFlow):
    """Options: map each door to an optional physical open/closed sensor."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        self._pending: list[str] = []
        self._new_options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        self._pending = list(self._entry.data[CONF_DOORS])
        self._new_options = {}
        return self._show_door_form()

    async def async_step_door(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        if user_input is not None:
            door = self._pending.pop(0)
            sensor = user_input.get("sensor")
            if sensor:
                self._new_options[f"{OPT_SENSOR_PREFIX}{door}"] = sensor

        if not self._pending:
            return self.async_create_entry(title="", data=self._new_options)
        return self._show_door_form()

    def _show_door_form(self) -> dict:
        door = self._pending[0]
        current = self._entry.options.get(f"{OPT_SENSOR_PREFIX}{door}")
        schema = vol.Schema(
            {vol.Optional("sensor"): EntitySelector(
                EntitySelectorConfig(domain=["binary_sensor", "sensor"])
            )}
        )
        if current:
            schema = self.add_suggested_values_to_schema(schema, {"sensor": current})
        return self.async_show_form(
            step_id="door",
            data_schema=schema,
            description_placeholders={"door": door.capitalize()},
        )
