"""Config flow for CitrineOS integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .citrine_api import CitrineApiError, CitrineAuthError, CitrineClient
from .const import (
    CONF_AUTH_TOKEN,
    CONF_BASE_URL,
    CONF_BATTERY_MIN_SOC,
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_CONTROLLER_INTERVAL_SECS,
    CONF_CONTROLLER_MODE,
    CONF_DEADBAND_W,
    CONF_DEFAULT_EVSE_ID,
    CONF_DEFAULT_ID_TAG,
    CONF_EMS_ENDPOINT_PREFIX,
    CONF_EMS_TELEMETRY_LIMIT,
    CONF_EMS_TELEMETRY_SITE_ID,
    CONF_EMS_TELEMETRY_STALE_SECS,
    CONF_GRID_POWER_SENSOR,
    CONF_HASURA_QUERY,
    CONF_HASURA_TOKEN,
    CONF_HASURA_URL,
    CONF_MAIN_FUSE_LIMIT_W,
    CONF_MIN_CHARGE_CURRENT_A,
    CONF_MIN_DWELL_SECS,
    CONF_NAME,
    CONF_NOMINAL_VOLTAGE,
    CONF_RAMP_RATE_W_S,
    CONF_REQUEST_TIMEOUT,
    CONF_SCAN_INTERVAL,
    CONF_SITE_EXPORT_LIMIT_W,
    CONF_SITE_ID,
    CONF_SITE_PHASES,
    CONF_SOLAR_POWER_SENSOR,
    CONF_SOLAR_START_BUFFER_W,
    CONF_TENANT_ID,
    CONF_VERIFY_SSL,
    CONTROLLER_MODES,
    DEFAULT_BATTERY_MIN_SOC,
    DEFAULT_CONTROLLER_INTERVAL_SECS,
    DEFAULT_CONTROLLER_MODE,
    DEFAULT_DEADBAND_W,
    DEFAULT_DEFAULT_EVSE_ID,
    DEFAULT_DEFAULT_ID_TAG,
    DEFAULT_EMS_ENDPOINT_PREFIX,
    DEFAULT_EMS_TELEMETRY_LIMIT,
    DEFAULT_EMS_TELEMETRY_STALE_SECS,
    DEFAULT_HASURA_QUERY,
    DEFAULT_MAIN_FUSE_LIMIT_W,
    DEFAULT_MIN_CHARGE_CURRENT_A,
    DEFAULT_MIN_DWELL_SECS,
    DEFAULT_NAME,
    DEFAULT_NOMINAL_VOLTAGE,
    DEFAULT_RAMP_RATE_W_S,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SITE_EXPORT_LIMIT_W,
    DEFAULT_SITE_ID,
    DEFAULT_SITE_PHASES,
    DEFAULT_SOLAR_START_BUFFER_W,
    DEFAULT_TENANT_ID,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .hasura_client import HasuraAuthError, HasuraClient, HasuraError


class CitrineConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CitrineOS."""

    VERSION = 1

    def __init__(self) -> None:
        self._user_data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Step 1: Connect to CitrineOS."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await self._async_validate(user_input)
            except CitrineAuthError:
                errors["base"] = "invalid_auth"
            except (CitrineApiError, HasuraError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(
                    f"{user_input[CONF_BASE_URL]}::{user_input[CONF_TENANT_ID]}"
                )
                self._abort_if_unique_id_configured()
                self._user_data = user_input
                return await self.async_step_load_control()

        return self.async_show_form(
            step_id="user",
            data_schema=self._user_schema(user_input),
            errors=errors,
        )

    async def async_step_load_control(self, user_input: dict[str, Any] | None = None):
        """Step 2: Configure local load controller & site sensors."""
        if user_input is not None:
            combined_data = {**self._user_data, **user_input}
            return self.async_create_entry(
                title=self._user_data.get(CONF_NAME, DEFAULT_NAME),
                data=combined_data,
            )

        return self.async_show_form(
            step_id="load_control",
            data_schema=self._load_control_schema(),
        )

    @staticmethod
    def _user_schema(user_input: dict[str, Any] | None) -> vol.Schema:
        user_input = user_input or {}
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default=user_input.get(CONF_NAME, DEFAULT_NAME)): str,
                vol.Required(CONF_BASE_URL, default=user_input.get(CONF_BASE_URL, "http://localhost:8080")): str,
                vol.Required(CONF_TENANT_ID, default=user_input.get(CONF_TENANT_ID, DEFAULT_TENANT_ID)): int,
                vol.Optional(CONF_AUTH_TOKEN, default=user_input.get(CONF_AUTH_TOKEN, "")): str,
                vol.Required(CONF_VERIFY_SSL, default=user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)): bool,
                vol.Required(CONF_REQUEST_TIMEOUT, default=user_input.get(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT)): int,
                vol.Optional(CONF_HASURA_URL, default=user_input.get(CONF_HASURA_URL, "")): str,
                vol.Optional(CONF_HASURA_TOKEN, default=user_input.get(CONF_HASURA_TOKEN, "")): str,
                vol.Optional(CONF_SCAN_INTERVAL, default=user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): int,
                vol.Optional(CONF_DEFAULT_ID_TAG, default=user_input.get(CONF_DEFAULT_ID_TAG, DEFAULT_DEFAULT_ID_TAG)): str,
                vol.Optional(CONF_DEFAULT_EVSE_ID, default=user_input.get(CONF_DEFAULT_EVSE_ID, DEFAULT_DEFAULT_EVSE_ID)): int,
            }
        )

    @staticmethod
    def _load_control_schema() -> vol.Schema:
        return vol.Schema(
            {
                vol.Optional(CONF_GRID_POWER_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_SOLAR_POWER_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_BATTERY_POWER_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_BATTERY_SOC_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_CONTROLLER_MODE, default=DEFAULT_CONTROLLER_MODE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=CONTROLLER_MODES,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_MAIN_FUSE_LIMIT_W, default=DEFAULT_MAIN_FUSE_LIMIT_W): vol.Coerce(float),
                vol.Optional(CONF_SOLAR_START_BUFFER_W, default=DEFAULT_SOLAR_START_BUFFER_W): vol.Coerce(float),
                vol.Optional(CONF_SITE_PHASES, default=DEFAULT_SITE_PHASES): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["1", "3"],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    async def _async_validate(self, data: dict[str, Any]) -> None:
        verify_ssl = bool(data.get(CONF_VERIFY_SSL, True))
        session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)

        client = CitrineClient(
            session,
            base_url=data[CONF_BASE_URL],
            tenant_id=int(data[CONF_TENANT_ID]),
            auth_token=data.get(CONF_AUTH_TOKEN) or None,
            verify_ssl=verify_ssl,
            request_timeout=int(data[CONF_REQUEST_TIMEOUT]),
        )
        await client.ping()

        hasura_url = data.get(CONF_HASURA_URL) or ""
        if hasura_url.strip():
            hasura_client = HasuraClient(
                session,
                url=hasura_url,
                token=data.get(CONF_HASURA_TOKEN) or None,
                request_timeout=int(data[CONF_REQUEST_TIMEOUT]),
                verify_ssl=verify_ssl,
            )
            try:
                await hasura_client.ping()
            except HasuraAuthError as err:
                raise CitrineAuthError(str(err)) from err

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return CitrineOptionsFlow(config_entry)


class CitrineOptionsFlow(config_entries.OptionsFlow):
    """Handle Citrine options flow."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = dict(self._config_entry.options)
        data = dict(self._config_entry.data)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    # Load Controller Settings
                    vol.Optional(
                        CONF_GRID_POWER_SENSOR,
                        description={"suggested_value": options.get(CONF_GRID_POWER_SENSOR, data.get(CONF_GRID_POWER_SENSOR))},
                    ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_SOLAR_POWER_SENSOR,
                        description={"suggested_value": options.get(CONF_SOLAR_POWER_SENSOR, data.get(CONF_SOLAR_POWER_SENSOR))},
                    ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_BATTERY_POWER_SENSOR,
                        description={"suggested_value": options.get(CONF_BATTERY_POWER_SENSOR, data.get(CONF_BATTERY_POWER_SENSOR))},
                    ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_BATTERY_SOC_SENSOR,
                        description={"suggested_value": options.get(CONF_BATTERY_SOC_SENSOR, data.get(CONF_BATTERY_SOC_SENSOR))},
                    ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_CONTROLLER_MODE,
                        default=options.get(CONF_CONTROLLER_MODE, data.get(CONF_CONTROLLER_MODE, DEFAULT_CONTROLLER_MODE)),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=CONTROLLER_MODES,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_MAIN_FUSE_LIMIT_W,
                        default=options.get(CONF_MAIN_FUSE_LIMIT_W, data.get(CONF_MAIN_FUSE_LIMIT_W, DEFAULT_MAIN_FUSE_LIMIT_W)),
                    ): vol.Coerce(float),
                    vol.Optional(
                        CONF_SOLAR_START_BUFFER_W,
                        default=options.get(CONF_SOLAR_START_BUFFER_W, data.get(CONF_SOLAR_START_BUFFER_W, DEFAULT_SOLAR_START_BUFFER_W)),
                    ): vol.Coerce(float),
                    vol.Optional(
                        CONF_MIN_CHARGE_CURRENT_A,
                        default=options.get(CONF_MIN_CHARGE_CURRENT_A, data.get(CONF_MIN_CHARGE_CURRENT_A, DEFAULT_MIN_CHARGE_CURRENT_A)),
                    ): vol.Coerce(float),
                    vol.Optional(
                        CONF_RAMP_RATE_W_S,
                        default=options.get(CONF_RAMP_RATE_W_S, data.get(CONF_RAMP_RATE_W_S, DEFAULT_RAMP_RATE_W_S)),
                    ): vol.Coerce(float),
                    vol.Optional(
                        CONF_CONTROLLER_INTERVAL_SECS,
                        default=options.get(CONF_CONTROLLER_INTERVAL_SECS, data.get(CONF_CONTROLLER_INTERVAL_SECS, DEFAULT_CONTROLLER_INTERVAL_SECS)),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
                    vol.Optional(
                        CONF_DEADBAND_W,
                        default=options.get(CONF_DEADBAND_W, data.get(CONF_DEADBAND_W, DEFAULT_DEADBAND_W)),
                    ): vol.Coerce(float),
                    vol.Optional(
                        CONF_MIN_DWELL_SECS,
                        default=options.get(CONF_MIN_DWELL_SECS, data.get(CONF_MIN_DWELL_SECS, DEFAULT_MIN_DWELL_SECS)),
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=600)),
                    # MQTT Intent Bus
                    vol.Optional(
                        CONF_SITE_ID,
                        default=options.get(CONF_SITE_ID, data.get(CONF_SITE_ID, DEFAULT_SITE_ID)),
                    ): str,
                    vol.Optional(
                        CONF_MQTT_TOPIC_PREFIX,
                        default=options.get(CONF_MQTT_TOPIC_PREFIX, data.get(CONF_MQTT_TOPIC_PREFIX, DEFAULT_MQTT_TOPIC_PREFIX)),
                    ): str,
                    # Connection & Discovery Settings
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
                    ): int,
                    vol.Optional(
                        CONF_DEFAULT_ID_TAG,
                        default=options.get(CONF_DEFAULT_ID_TAG, data.get(CONF_DEFAULT_ID_TAG, DEFAULT_DEFAULT_ID_TAG)),
                    ): str,
                    vol.Optional(
                        CONF_DEFAULT_EVSE_ID,
                        default=options.get(CONF_DEFAULT_EVSE_ID, data.get(CONF_DEFAULT_EVSE_ID, DEFAULT_DEFAULT_EVSE_ID)),
                    ): int,
                }
            ),
        )

