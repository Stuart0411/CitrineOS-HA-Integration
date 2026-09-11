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
    CONF_DOE_EXPORT_LIMIT_SENSOR,
    CONF_DOE_IMPORT_LIMIT_SENSOR,
    CONF_EMS_ENDPOINT_PREFIX,
    CONF_EMS_TELEMETRY_LIMIT,
    CONF_EMS_TELEMETRY_SITE_ID,
    CONF_EMS_TELEMETRY_STALE_SECS,
    CONF_GRID_PHASE_A_CURRENT_SENSOR,
    CONF_GRID_PHASE_B_CURRENT_SENSOR,
    CONF_GRID_PHASE_C_CURRENT_SENSOR,
    CONF_GRID_POWER_SENSOR,
    CONF_HASURA_QUERY,
    CONF_HASURA_TOKEN,
    CONF_HASURA_URL,
    CONF_MAIN_FUSE_CURRENT_A,
    CONF_MAIN_FUSE_LIMIT_W,
    CONF_MAX_PHASE_UNBALANCE_A,
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
    DEFAULT_MAIN_FUSE_CURRENT_A,
    DEFAULT_MAIN_FUSE_LIMIT_W,
    DEFAULT_MAX_PHASE_UNBALANCE_A,
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
                vol.Required(CONF_NAME, default=user_input.get(CONF_NAME, DEFAULT_NAME)): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Required(CONF_BASE_URL, default=user_input.get(CONF_BASE_URL, "http://localhost:8080")): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
                ),
                vol.Required(CONF_TENANT_ID, default=user_input.get(CONF_TENANT_ID, DEFAULT_TENANT_ID)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=999999, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_AUTH_TOKEN): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_VERIFY_SSL, default=user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)): selector.BooleanSelector(),
                vol.Required(CONF_REQUEST_TIMEOUT, default=user_input.get(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=120, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_HASURA_URL): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
                ),
                vol.Optional(CONF_HASURA_TOKEN): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_SCAN_INTERVAL, default=user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=3600, step=5, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_DEFAULT_ID_TAG, default=user_input.get(CONF_DEFAULT_ID_TAG, DEFAULT_DEFAULT_ID_TAG)): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional(CONF_DEFAULT_EVSE_ID, default=user_input.get(CONF_DEFAULT_EVSE_ID, DEFAULT_DEFAULT_EVSE_ID)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=999, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
            }
        )

    @staticmethod
    def _load_control_schema() -> vol.Schema:
        return vol.Schema(
            {
                vol.Optional(CONF_GRID_POWER_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_GRID_PHASE_A_CURRENT_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_GRID_PHASE_B_CURRENT_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_GRID_PHASE_C_CURRENT_SENSOR): selector.EntitySelector(
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
                vol.Optional(CONF_DOE_IMPORT_LIMIT_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_DOE_EXPORT_LIMIT_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_CONTROLLER_MODE, default=DEFAULT_CONTROLLER_MODE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=CONTROLLER_MODES,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_MAIN_FUSE_LIMIT_W, default=DEFAULT_MAIN_FUSE_LIMIT_W): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1000, max=250000, step=100, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_SOLAR_START_BUFFER_W, default=DEFAULT_SOLAR_START_BUFFER_W): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=5000, step=50, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_SITE_PHASES, default=str(DEFAULT_SITE_PHASES)): selector.SelectSelector(
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
                        CONF_GRID_PHASE_A_CURRENT_SENSOR,
                        description={"suggested_value": options.get(CONF_GRID_PHASE_A_CURRENT_SENSOR, data.get(CONF_GRID_PHASE_A_CURRENT_SENSOR))},
                    ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_GRID_PHASE_B_CURRENT_SENSOR,
                        description={"suggested_value": options.get(CONF_GRID_PHASE_B_CURRENT_SENSOR, data.get(CONF_GRID_PHASE_B_CURRENT_SENSOR))},
                    ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_GRID_PHASE_C_CURRENT_SENSOR,
                        description={"suggested_value": options.get(CONF_GRID_PHASE_C_CURRENT_SENSOR, data.get(CONF_GRID_PHASE_C_CURRENT_SENSOR))},
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
                        CONF_DOE_IMPORT_LIMIT_SENSOR,
                        description={"suggested_value": options.get(CONF_DOE_IMPORT_LIMIT_SENSOR, data.get(CONF_DOE_IMPORT_LIMIT_SENSOR))},
                    ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_DOE_EXPORT_LIMIT_SENSOR,
                        description={"suggested_value": options.get(CONF_DOE_EXPORT_LIMIT_SENSOR, data.get(CONF_DOE_EXPORT_LIMIT_SENSOR))},
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
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=1000, max=250000, step=100, mode=selector.NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_MAIN_FUSE_CURRENT_A,
                        default=options.get(CONF_MAIN_FUSE_CURRENT_A, data.get(CONF_MAIN_FUSE_CURRENT_A, DEFAULT_MAIN_FUSE_CURRENT_A)),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=10, max=500, step=1, mode=selector.NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_MAX_PHASE_UNBALANCE_A,
                        default=options.get(CONF_MAX_PHASE_UNBALANCE_A, data.get(CONF_MAX_PHASE_UNBALANCE_A, DEFAULT_MAX_PHASE_UNBALANCE_A)),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=0, max=100, step=1, mode=selector.NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_SOLAR_START_BUFFER_W,
                        default=options.get(CONF_SOLAR_START_BUFFER_W, data.get(CONF_SOLAR_START_BUFFER_W, DEFAULT_SOLAR_START_BUFFER_W)),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=0, max=5000, step=50, mode=selector.NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_MIN_CHARGE_CURRENT_A,
                        default=options.get(CONF_MIN_CHARGE_CURRENT_A, data.get(CONF_MIN_CHARGE_CURRENT_A, DEFAULT_MIN_CHARGE_CURRENT_A)),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=6, max=16, step=1, mode=selector.NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_RAMP_RATE_W_S,
                        default=options.get(CONF_RAMP_RATE_W_S, data.get(CONF_RAMP_RATE_W_S, DEFAULT_RAMP_RATE_W_S)),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=50, max=10000, step=50, mode=selector.NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_CONTROLLER_INTERVAL_SECS,
                        default=options.get(CONF_CONTROLLER_INTERVAL_SECS, data.get(CONF_CONTROLLER_INTERVAL_SECS, DEFAULT_CONTROLLER_INTERVAL_SECS)),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=1, max=60, step=1, mode=selector.NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_DEADBAND_W,
                        default=options.get(CONF_DEADBAND_W, data.get(CONF_DEADBAND_W, DEFAULT_DEADBAND_W)),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=0, max=2000, step=50, mode=selector.NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_MIN_DWELL_SECS,
                        default=options.get(CONF_MIN_DWELL_SECS, data.get(CONF_MIN_DWELL_SECS, DEFAULT_MIN_DWELL_SECS)),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=5, max=600, step=5, mode=selector.NumberSelectorMode.BOX)
                    ),
                    # MQTT Intent Bus
                    vol.Optional(
                        CONF_SITE_ID,
                        default=options.get(CONF_SITE_ID, data.get(CONF_SITE_ID, DEFAULT_SITE_ID)),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                    ),
                    vol.Optional(
                        CONF_MQTT_TOPIC_PREFIX,
                        default=options.get(CONF_MQTT_TOPIC_PREFIX, data.get(CONF_MQTT_TOPIC_PREFIX, DEFAULT_MQTT_TOPIC_PREFIX)),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                    ),
                    # Connection & Discovery Settings
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=5, max=3600, step=5, mode=selector.NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_DEFAULT_ID_TAG,
                        default=options.get(CONF_DEFAULT_ID_TAG, data.get(CONF_DEFAULT_ID_TAG, DEFAULT_DEFAULT_ID_TAG)),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                    ),
                    vol.Optional(
                        CONF_DEFAULT_EVSE_ID,
                        default=options.get(CONF_DEFAULT_EVSE_ID, data.get(CONF_DEFAULT_EVSE_ID, DEFAULT_DEFAULT_EVSE_ID)),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=0, max=999, step=1, mode=selector.NumberSelectorMode.BOX)
                    ),
                }
            ),
        )

