"""CitrineOS Home Assistant integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .citrine_api import CitrineApiError, CitrineClient
from .const import (
    ATTR_DURATION,
    ATTR_ENTRY_ID,
    ATTR_EVSE_ID,
    ATTR_GROUP_ID,
    ATTR_ID_TAG,
    ATTR_LIMIT,
    ATTR_PROFILE_ID,
    ATTR_PROFILE_KIND,
    ATTR_PROFILE_PURPOSE,
    ATTR_PROTOCOL,
    ATTR_STACK_LEVEL,
    ATTR_STATION_ID,
    ATTR_STATION_IDS,
    ATTR_TRANSACTION_ID,
    ATTR_UNIT,
    CONF_AUTH_TOKEN,
    CONF_BASE_URL,
    CONF_DEFAULT_EVSE_ID,
    CONF_DEFAULT_ID_TAG,
    CONF_HASURA_TOKEN,
    CONF_HASURA_URL,
    CONF_REQUEST_TIMEOUT,
    CONF_TENANT_ID,
    CONF_VERIFY_SSL,
    DEFAULT_DEFAULT_EVSE_ID,
    DEFAULT_DEFAULT_ID_TAG,
    DOMAIN,
    PLATFORMS,
    SERVICE_SET_GROUP_LIMIT,
    SERVICE_SET_CHARGING_PROFILE,
    SERVICE_CLEAR_CHARGING_PROFILE,
    SERVICE_SET_STATION_LIMIT,
    SERVICE_START_CHARGING,
    SERVICE_STOP_CHARGING,
    SERVICE_SYNC_DISCOVERY_NOW,
)
from .coordinator import CitrineCoordinator
from .hasura_client import HasuraClient

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up integration (YAML not used)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CitrineOS from a config entry."""
    verify_ssl = bool(entry.data.get(CONF_VERIFY_SSL, True))
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)

    client = CitrineClient(
        session,
        base_url=entry.data[CONF_BASE_URL],
        tenant_id=int(entry.data[CONF_TENANT_ID]),
        auth_token=entry.data.get(CONF_AUTH_TOKEN),
        verify_ssl=verify_ssl,
        request_timeout=int(entry.data[CONF_REQUEST_TIMEOUT]),
    )

    hasura_client: HasuraClient | None = None
    if entry.data.get(CONF_HASURA_URL):
        hasura_client = HasuraClient(
            session,
            url=entry.data[CONF_HASURA_URL],
            token=entry.data.get(CONF_HASURA_TOKEN),
            request_timeout=int(entry.data[CONF_REQUEST_TIMEOUT]),
            verify_ssl=verify_ssl,
        )

    coordinator = CitrineCoordinator(
        hass,
        hasura_client=hasura_client,
        entry_data=dict(entry.data),
        entry_options=dict(entry.options),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:  # noqa: BLE001
        raise ConfigEntryNotReady(f"Initial Citrine refresh failed: {err}") from err

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "hasura_client": hasura_client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services once."""

    def _register(name: str, handler: Any, schema: vol.Schema) -> None:
        if hass.services.has_service(DOMAIN, name):
            return
        hass.services.async_register(DOMAIN, name, handler, schema=schema)

    async def async_handle_start(call: ServiceCall) -> None:
        ctx = _resolve_context(hass, call)
        coordinator = ctx["coordinator"]
        client: CitrineClient = ctx["client"]

        station_id = call.data[ATTR_STATION_ID]
        protocol = client.normalize_protocol(
            call.data.get(ATTR_PROTOCOL) or _station_protocol(coordinator, station_id)
        )

        id_tag = call.data.get(ATTR_ID_TAG) or ctx["entry"].options.get(
            CONF_DEFAULT_ID_TAG,
            DEFAULT_DEFAULT_ID_TAG,
        )
        explicit_evse_id = call.data.get(ATTR_EVSE_ID)
        configured_evse_id = ctx["entry"].options.get(
            CONF_DEFAULT_EVSE_ID,
            ctx["entry"].data.get(CONF_DEFAULT_EVSE_ID, DEFAULT_DEFAULT_EVSE_ID),
        )
        evse_id = int(
            explicit_evse_id
            if explicit_evse_id is not None
            else configured_evse_id
            if configured_evse_id is not None
            else _station_default_evse_id(coordinator, station_id)
            or DEFAULT_DEFAULT_EVSE_ID
        )
        remote_start_id = _consume_next_remote_start_id(coordinator, station_id)

        await client.request_start_transaction(
            protocol=protocol,
            station_id=station_id,
            id_tag=id_tag,
            evse_id=evse_id,
            remote_start_id=remote_start_id,
        )

    async def async_handle_stop(call: ServiceCall) -> None:
        ctx = _resolve_context(hass, call)
        coordinator = ctx["coordinator"]
        client: CitrineClient = ctx["client"]

        station_id = call.data[ATTR_STATION_ID]
        protocol = client.normalize_protocol(
            call.data.get(ATTR_PROTOCOL) or _station_protocol(coordinator, station_id)
        )

        transaction_id = call.data.get(ATTR_TRANSACTION_ID) or _station_current_transaction_id(
            coordinator,
            station_id,
        )
        if transaction_id is None:
            raise HomeAssistantError(
                "Transaction id is required when no current/previous transaction is discoverable"
            )

        await client.request_stop_transaction(
            protocol=protocol,
            station_id=station_id,
            transaction_id=str(transaction_id),
        )

    async def async_handle_set_station_limit(call: ServiceCall) -> None:
        ctx = _resolve_context(hass, call)
        coordinator = ctx["coordinator"]
        client: CitrineClient = ctx["client"]

        station_id = call.data[ATTR_STATION_ID]
        protocol = client.normalize_protocol(
            call.data.get(ATTR_PROTOCOL) or _station_protocol(coordinator, station_id)
        )

        await client.set_station_limit(
            protocol=protocol,
            station_id=station_id,
            limit=float(call.data[ATTR_LIMIT]),
            unit=str(call.data.get(ATTR_UNIT, "W")),
            evse_id=int(call.data.get(ATTR_EVSE_ID, 0)),
            duration=int(call.data.get(ATTR_DURATION, 300)),
        )

    async def async_handle_set_charging_profile(call: ServiceCall) -> None:
        ctx = _resolve_context(hass, call)
        coordinator = ctx["coordinator"]
        client: CitrineClient = ctx["client"]

        station_id = call.data[ATTR_STATION_ID]
        protocol = client.normalize_protocol(
            call.data.get(ATTR_PROTOCOL) or _station_protocol(coordinator, station_id)
        )
        capabilities = _station_capabilities(coordinator, station_id)
        requested_unit = str(call.data.get(ATTR_UNIT, "W")).upper()
        allowed_units = [str(unit).upper() for unit in capabilities.get("allowed_units", [])]
        if allowed_units and requested_unit not in allowed_units:
            requested_unit = str(capabilities.get("preferred_unit", allowed_units[0])).upper()

        requested_purpose = call.data.get(ATTR_PROFILE_PURPOSE)
        supported_purposes = capabilities.get("supported_profile_purposes", [])
        if requested_purpose and supported_purposes and requested_purpose not in supported_purposes:
            requested_purpose = str(capabilities.get("default_profile_purpose", supported_purposes[0]))

        requested_kind = call.data.get(ATTR_PROFILE_KIND)
        if requested_kind is not None:
            requested_kind = str(requested_kind).strip().capitalize()
        supported_kinds = capabilities.get("supported_profile_kinds", [])
        if requested_kind and supported_kinds and requested_kind not in supported_kinds:
            requested_kind = str(capabilities.get("default_profile_kind", supported_kinds[0]))

        transaction_id = call.data.get(ATTR_TRANSACTION_ID) or _station_current_transaction_id(
            coordinator,
            station_id,
        )

        # TxProfile usually requires a transaction id. If unavailable, fallback to a non-transaction profile.
        if requested_purpose == "TxProfile" and transaction_id is None:
            fallback_purpose = str(capabilities.get("default_profile_purpose", "TxDefaultProfile"))
            if fallback_purpose == "TxProfile":
                fallback_purpose = next(
                    (
                        purpose
                        for purpose in supported_purposes
                        if purpose != "TxProfile"
                    ),
                    "TxDefaultProfile",
                )
            requested_purpose = fallback_purpose

        limit_value = float(call.data[ATTR_LIMIT])
        supports_bidirectional = bool(capabilities.get("supports_bidirectional_power_transfer", False))
        if limit_value < 0 and not supports_bidirectional:
            raise HomeAssistantError(
                f"Station {station_id} does not advertise bidirectional profile support; negative limits are not allowed"
            )

        min_profile_limit = capabilities.get("min_profile_limit")
        max_profile_limit = capabilities.get("max_profile_limit")
        if min_profile_limit is not None:
            limit_value = max(float(min_profile_limit), limit_value)
        if max_profile_limit is not None:
            limit_value = min(float(max_profile_limit), limit_value)

        await client.set_charging_profile(
            protocol=protocol,
            station_id=station_id,
            limit=limit_value,
            unit=requested_unit,
            evse_id=int(call.data.get(ATTR_EVSE_ID, 0)),
            duration=int(call.data.get(ATTR_DURATION, 300)),
            stack_level=int(call.data.get(ATTR_STACK_LEVEL, 1)),
            profile_id=call.data.get(ATTR_PROFILE_ID),
            profile_purpose=requested_purpose,
            profile_kind=requested_kind,
            transaction_id=str(transaction_id) if transaction_id is not None else None,
        )

    async def async_handle_clear_charging_profile(call: ServiceCall) -> None:
        ctx = _resolve_context(hass, call)
        coordinator = ctx["coordinator"]
        client: CitrineClient = ctx["client"]

        station_id = call.data[ATTR_STATION_ID]
        protocol = client.normalize_protocol(
            call.data.get(ATTR_PROTOCOL) or _station_protocol(coordinator, station_id)
        )

        await client.clear_charging_profile(
            protocol=protocol,
            station_id=station_id,
            evse_id=int(call.data.get(ATTR_EVSE_ID, 0)),
            profile_id=call.data.get(ATTR_PROFILE_ID),
            stack_level=call.data.get(ATTR_STACK_LEVEL),
            profile_purpose=call.data.get(ATTR_PROFILE_PURPOSE),
        )

    async def async_handle_set_group_limit(call: ServiceCall) -> None:
        ctx = _resolve_context(hass, call)
        coordinator = ctx["coordinator"]
        client: CitrineClient = ctx["client"]

        station_ids = call.data[ATTR_STATION_IDS]
        protocols = {
            station_id: _station_protocol(coordinator, station_id)
            for station_id in station_ids
        }
        known_protocols = {
            station_id: protocol
            for station_id, protocol in protocols.items()
            if protocol is not None
        }

        await client.set_group_limit(
            group_id=str(call.data[ATTR_GROUP_ID]),
            station_protocols=known_protocols,
            total_limit=float(call.data[ATTR_LIMIT]),
            unit=str(call.data.get(ATTR_UNIT, "W")),
            duration=int(call.data.get(ATTR_DURATION, 300)),
        )

    async def async_handle_sync(call: ServiceCall) -> None:
        ctx = _resolve_context(hass, call)
        coordinator = ctx["coordinator"]
        await coordinator.async_request_refresh()

    _register(
        SERVICE_START_CHARGING,
        async_handle_start,
        vol.Schema(
            {
                vol.Required(ATTR_STATION_ID): str,
                vol.Optional(ATTR_ENTRY_ID): str,
                vol.Optional(ATTR_PROTOCOL): str,
                vol.Optional(ATTR_ID_TAG): str,
                vol.Optional(ATTR_EVSE_ID): int,
            }
        ),
    )

    _register(
        SERVICE_STOP_CHARGING,
        async_handle_stop,
        vol.Schema(
            {
                vol.Required(ATTR_STATION_ID): str,
                vol.Optional(ATTR_TRANSACTION_ID): str,
                vol.Optional(ATTR_ENTRY_ID): str,
                vol.Optional(ATTR_PROTOCOL): str,
            }
        ),
    )

    _register(
        SERVICE_SET_STATION_LIMIT,
        async_handle_set_station_limit,
        vol.Schema(
            {
                vol.Required(ATTR_STATION_ID): str,
                vol.Required(ATTR_LIMIT): vol.Coerce(float),
                vol.Optional(ATTR_ENTRY_ID): str,
                vol.Optional(ATTR_PROTOCOL): str,
                vol.Optional(ATTR_EVSE_ID, default=0): int,
                vol.Optional(ATTR_UNIT, default="W"): str,
                vol.Optional(ATTR_DURATION, default=300): int,
            }
        ),
    )

    _register(
        SERVICE_SET_GROUP_LIMIT,
        async_handle_set_group_limit,
        vol.Schema(
            {
                vol.Required(ATTR_GROUP_ID): str,
                vol.Required(ATTR_STATION_IDS): [str],
                vol.Required(ATTR_LIMIT): vol.Coerce(float),
                vol.Optional(ATTR_ENTRY_ID): str,
                vol.Optional(ATTR_UNIT, default="W"): str,
                vol.Optional(ATTR_DURATION, default=300): int,
            }
        ),
    )

    _register(
        SERVICE_SET_CHARGING_PROFILE,
        async_handle_set_charging_profile,
        vol.Schema(
            {
                vol.Required(ATTR_STATION_ID): str,
                vol.Required(ATTR_LIMIT): vol.Coerce(float),
                vol.Optional(ATTR_ENTRY_ID): str,
                vol.Optional(ATTR_PROTOCOL): str,
                vol.Optional(ATTR_EVSE_ID, default=0): int,
                vol.Optional(ATTR_UNIT, default="W"): str,
                vol.Optional(ATTR_DURATION, default=300): int,
                vol.Optional(ATTR_TRANSACTION_ID): str,
                vol.Optional(ATTR_STACK_LEVEL, default=1): int,
                vol.Optional(ATTR_PROFILE_ID): int,
                vol.Optional(ATTR_PROFILE_PURPOSE): str,
                vol.Optional(ATTR_PROFILE_KIND): str,
            }
        ),
    )

    _register(
        SERVICE_CLEAR_CHARGING_PROFILE,
        async_handle_clear_charging_profile,
        vol.Schema(
            {
                vol.Required(ATTR_STATION_ID): str,
                vol.Optional(ATTR_ENTRY_ID): str,
                vol.Optional(ATTR_PROTOCOL): str,
                vol.Optional(ATTR_EVSE_ID, default=0): int,
                vol.Optional(ATTR_STACK_LEVEL): int,
                vol.Optional(ATTR_PROFILE_ID): int,
                vol.Optional(ATTR_PROFILE_PURPOSE): str,
            }
        ),
    )

    _register(
        SERVICE_SYNC_DISCOVERY_NOW,
        async_handle_sync,
        vol.Schema({vol.Optional(ATTR_ENTRY_ID): str}),
    )


def _resolve_context(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    entry_id = call.data.get(ATTR_ENTRY_ID)
    if entry_id:
        data = hass.data[DOMAIN].get(entry_id)
        if not data:
            raise HomeAssistantError(f"Entry not found: {entry_id}")
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            raise HomeAssistantError(f"Entry metadata not found: {entry_id}")
        return {**data, "entry": entry}

    if not hass.data[DOMAIN]:
        raise HomeAssistantError("No CitrineOS config entries loaded")

    first_entry_id, data = next(iter(hass.data[DOMAIN].items()))
    entry = hass.config_entries.async_get_entry(first_entry_id)
    if entry is None:
        raise HomeAssistantError("Default entry metadata not found")
    return {**data, "entry": entry}


def _station_protocol(coordinator: CitrineCoordinator, station_id: str) -> str | None:
    station = _station_record(coordinator, station_id)
    cached = coordinator.get_station_protocol(station_id)
    if cached:
        return cached
    if station is None:
        return None
    protocol = station.get("protocol")
    return str(protocol) if protocol else None


def _station_capabilities(coordinator: CitrineCoordinator, station_id: str) -> dict[str, Any]:
    return coordinator.get_station_capabilities(station_id)


def _station_default_evse_id(coordinator: CitrineCoordinator, station_id: str) -> int | None:
    station = _station_record(coordinator, station_id)
    if station is None:
        return None

    default_evse = station.get("defaultEvseId")
    if default_evse is not None:
        try:
            return int(default_evse)
        except (TypeError, ValueError):
            pass

    for connector in station.get("connectors", []):
        evse_id = connector.get("evseId") or connector.get("connectorId")
        if evse_id is None:
            continue
        try:
            return int(evse_id)
        except (TypeError, ValueError):
            continue
    return None


def _station_current_transaction_id(
    coordinator: CitrineCoordinator,
    station_id: str,
) -> str | None:
    station = _station_record(coordinator, station_id)
    if station is None:
        return None

    for key in ("activeTransactionId", "currentTransactionId", "transactionId", "previousTransactionId"):
        value = station.get(key)
        if value is not None:
            return str(value)
    return None


def _consume_next_remote_start_id(
    coordinator: CitrineCoordinator,
    station_id: str,
) -> int | None:
    station = _station_record(coordinator, station_id)
    if station is None:
        return None

    next_id = station.get("nextRemoteStartId")
    if next_id is None:
        return None

    try:
        value = int(next_id)
    except (TypeError, ValueError):
        return None

    station["nextRemoteStartId"] = value + 1
    return value


def _station_record(coordinator: CitrineCoordinator, station_id: str) -> dict[str, Any] | None:
    stations = coordinator.data.get("stations", []) if coordinator.data else []
    for station in stations:
        if str(station.get("id")) == str(station_id):
            return station
    return None
