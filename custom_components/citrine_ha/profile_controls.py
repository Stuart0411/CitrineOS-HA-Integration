"""Shared helpers for profile control payload dispatch."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    ATTR_DISCHARGE_LIMIT,
    ATTR_DURATION,
    ATTR_ENTRY_ID,
    ATTR_EVSE_ID,
    ATTR_LIMIT,
    ATTR_OPERATION_MODE,
    ATTR_PROFILE_ID,
    ATTR_PROFILE_KIND,
    ATTR_PROFILE_PERIODS,
    ATTR_PROFILE_PURPOSE,
    ATTR_PROTOCOL,
    ATTR_SETPOINT,
    ATTR_STACK_LEVEL,
    ATTR_STATION_ID,
    ATTR_TRANSACTION_ID,
    ATTR_UNIT,
    DEFAULT_PROFILE_DISCHARGE_LIMIT,
    DEFAULT_PROFILE_DURATION,
    DEFAULT_PROFILE_LIMIT,
    DEFAULT_PROFILE_OPERATION_MODE,
    DEFAULT_PROFILE_SETPOINT,
    DEFAULT_PROFILE_STACK_LEVEL,
    DEFAULT_PROFILE_UNIT,
    DOMAIN,
    SERVICE_SET_CHARGING_PROFILE,
)
from .coordinator import CitrineCoordinator


def build_profile_service_data(
    coordinator: CitrineCoordinator,
    entry: ConfigEntry,
    station_id: str,
) -> dict[str, Any]:
    """Build profile service payload from station preferences and live station context."""
    prefs = coordinator.get_station_profile_preferences(station_id)
    station = _station_record(coordinator, station_id)

    protocol = str(
        coordinator.get_station_protocol(station_id, station.get("protocol"))
        or station.get("protocol")
        or "ocpp2.0.1"
    )
    profile_kind = str(prefs.get("profile_kind", "Absolute"))
    is_ocpp21 = protocol == "ocpp2.1"

    transaction_id = (
        station.get("activeTransactionId")
        or station.get("currentTransactionId")
        or station.get("transactionId")
        or station.get("previousTransactionId")
    )

    data: dict[str, Any] = {
        ATTR_ENTRY_ID: entry.entry_id,
        ATTR_STATION_ID: station_id,
        ATTR_PROTOCOL: protocol,
        ATTR_LIMIT: float(prefs.get("limit", DEFAULT_PROFILE_LIMIT)),
        ATTR_UNIT: str(prefs.get("unit", DEFAULT_PROFILE_UNIT)),
        ATTR_EVSE_ID: int(prefs.get("evse_id", 0)),
        ATTR_DURATION: int(prefs.get("duration", DEFAULT_PROFILE_DURATION)),
        ATTR_STACK_LEVEL: int(prefs.get("stack_level", DEFAULT_PROFILE_STACK_LEVEL)),
        ATTR_PROFILE_PURPOSE: str(prefs.get("profile_purpose", "TxDefaultProfile")),
        ATTR_PROFILE_KIND: profile_kind,
    }

    if is_ocpp21 or profile_kind == "Dynamic":
        data[ATTR_SETPOINT] = float(prefs.get("setpoint", DEFAULT_PROFILE_SETPOINT))
        data[ATTR_DISCHARGE_LIMIT] = float(
            prefs.get("discharge_limit", DEFAULT_PROFILE_DISCHARGE_LIMIT)
        )
        data[ATTR_OPERATION_MODE] = str(
            prefs.get("operation_mode", DEFAULT_PROFILE_OPERATION_MODE)
        )

    profile_id = prefs.get("profile_id")
    if profile_id is not None:
        try:
            data[ATTR_PROFILE_ID] = int(profile_id)
        except (TypeError, ValueError):
            pass

    profile_periods = prefs.get("profile_periods")
    if isinstance(profile_periods, list):
        data[ATTR_PROFILE_PERIODS] = profile_periods

    if transaction_id is not None:
        data[ATTR_TRANSACTION_ID] = str(transaction_id)

    return data


async def async_push_profile_update(
    hass: HomeAssistant,
    coordinator: CitrineCoordinator,
    entry: ConfigEntry,
    station_id: str,
) -> None:
    """Push updated profile preferences to Citrine via service call."""
    data = build_profile_service_data(coordinator, entry, station_id)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_CHARGING_PROFILE,
        data,
        blocking=True,
    )


def _station_record(coordinator: CitrineCoordinator, station_id: str) -> dict[str, Any]:
    stations = coordinator.data.get("stations", []) if coordinator.data else []
    for station in stations:
        if str(station.get("id")) == str(station_id):
            return station
    return {"id": station_id}
