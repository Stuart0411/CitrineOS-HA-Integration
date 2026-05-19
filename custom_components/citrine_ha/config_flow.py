"""Config flow for CitrineOS integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .citrine_api import CitrineApiError, CitrineAuthError, CitrineClient
from .const import (
    CONF_AUTH_TOKEN,
    CONF_BASE_URL,
    CONF_DEFAULT_EVSE_ID,
    CONF_DEFAULT_ID_TAG,
    CONF_HASURA_QUERY,
    CONF_HASURA_TOKEN,
    CONF_HASURA_URL,
    CONF_NAME,
    CONF_REQUEST_TIMEOUT,
    CONF_SCAN_INTERVAL,
    CONF_TENANT_ID,
    CONF_VERIFY_SSL,
    DEFAULT_DEFAULT_EVSE_ID,
    DEFAULT_DEFAULT_ID_TAG,
    DEFAULT_HASURA_QUERY,
    DEFAULT_NAME,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TENANT_ID,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .hasura_client import HasuraAuthError, HasuraClient, HasuraError


class CitrineConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CitrineOS."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
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
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self._user_schema(user_input),
            errors=errors,
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
                vol.Optional(CONF_HASURA_QUERY, default=user_input.get(CONF_HASURA_QUERY, DEFAULT_HASURA_QUERY)): str,
                vol.Optional(CONF_SCAN_INTERVAL, default=user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): int,
                vol.Optional(CONF_DEFAULT_ID_TAG, default=user_input.get(CONF_DEFAULT_ID_TAG, DEFAULT_DEFAULT_ID_TAG)): str,
                vol.Optional(CONF_DEFAULT_EVSE_ID, default=user_input.get(CONF_DEFAULT_EVSE_ID, DEFAULT_DEFAULT_EVSE_ID)): int,
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
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=options.get(
                            CONF_SCAN_INTERVAL,
                            data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                        ),
                    ): int,
                    vol.Optional(
                        CONF_HASURA_QUERY,
                        default=options.get(
                            CONF_HASURA_QUERY,
                            data.get(CONF_HASURA_QUERY, DEFAULT_HASURA_QUERY),
                        ),
                    ): str,
                    vol.Optional(
                        CONF_DEFAULT_ID_TAG,
                        default=options.get(
                            CONF_DEFAULT_ID_TAG,
                            data.get(CONF_DEFAULT_ID_TAG, DEFAULT_DEFAULT_ID_TAG),
                        ),
                    ): str,
                    vol.Optional(
                        CONF_DEFAULT_EVSE_ID,
                        default=options.get(
                            CONF_DEFAULT_EVSE_ID,
                            data.get(CONF_DEFAULT_EVSE_ID, DEFAULT_DEFAULT_EVSE_ID),
                        ),
                    ): int,
                }
            ),
        )
