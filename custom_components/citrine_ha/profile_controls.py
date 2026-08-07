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


DER_STRATEGY_OPERATION_MODE: dict[str, str] = {
    "central_setpoint": "CentralSetpoint",
    "external_setpoint": "ExternalSetpoint",
    "external_limits": "ExternalLimits",
    "frequency_response": "CentralFrequency",
    "local_load_balancing": "LocalLoadBalancing",
}


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_profile_service_data(
    coordinator: CitrineCoordinator,
    entry: ConfigEntry,
    station_id: str,
) -> dict[str, Any]:
    """Build profile service payload from station preferences and live station context."""
    prefs = coordinator.get_station_profile_preferences(station_id)
    station = _station_record(coordinator, station_id)
    capabilities = coordinator.get_station_capabilities(station_id)

    protocol = str(
        coordinator.get_station_protocol(station_id, station.get("protocol"))
        or station.get("protocol")
        or "ocpp2.0.1"
    )
    profile_kind = str(prefs.get("profile_kind", "Absolute")).strip().capitalize()
    profile_purpose = str(prefs.get("profile_purpose", "TxDefaultProfile")).strip()
    is_ocpp21 = protocol == "ocpp2.1"
    der_strategy = str(prefs.get("der_strategy", "manual")).strip().lower() or "manual"

    if der_strategy != "manual" and profile_purpose != "TxProfile":
        if not bool(capabilities.get("supports_dynamic_profiles", False)):
            raise ValueError("Selected DER strategy requires dynamic profile support")
        profile_kind = "Dynamic"

    transaction_id = (
        station.get("activeTransactionId")
        or station.get("currentTransactionId")
        or station.get("transactionId")
        or station.get("previousTransactionId")
    )

    evse_id = int(prefs.get("evse_id", 0))
    if protocol != "ocpp1.6" and evse_id < 1:
        evse_id = int(station.get("defaultEvseId") or 1)

    limit_value = _as_float(prefs.get("limit"), DEFAULT_PROFILE_LIMIT) or DEFAULT_PROFILE_LIMIT
    setpoint_value = _as_float(prefs.get("setpoint"), DEFAULT_PROFILE_SETPOINT)
    discharge_limit_value = _as_float(
        prefs.get("discharge_limit"),
        DEFAULT_PROFILE_DISCHARGE_LIMIT,
    )

    operation_mode = str(prefs.get("operation_mode", DEFAULT_PROFILE_OPERATION_MODE)).strip()
    if der_strategy in DER_STRATEGY_OPERATION_MODE:
        operation_mode = DER_STRATEGY_OPERATION_MODE[der_strategy]

    if is_ocpp21 and profile_kind == "Dynamic":
        if operation_mode in {"CentralSetpoint", "ExternalSetpoint"} and setpoint_value is None:
            setpoint_value = float(limit_value)
        if discharge_limit_value is not None and discharge_limit_value > 0:
            discharge_limit_value = -discharge_limit_value
        if setpoint_value is not None and setpoint_value < 0 and discharge_limit_value is None:
            discharge_limit_value = float(setpoint_value)
        if der_strategy == "external_limits":
            setpoint_value = None

    data: dict[str, Any] = {
        ATTR_ENTRY_ID: entry.entry_id,
        ATTR_STATION_ID: station_id,
        ATTR_PROTOCOL: protocol,
        ATTR_LIMIT: float(limit_value),
        ATTR_UNIT: str(prefs.get("unit", DEFAULT_PROFILE_UNIT)),
        ATTR_EVSE_ID: evse_id,
        ATTR_DURATION: int(prefs.get("duration", DEFAULT_PROFILE_DURATION)),
        ATTR_STACK_LEVEL: int(prefs.get("stack_level", DEFAULT_PROFILE_STACK_LEVEL)),
        ATTR_PROFILE_PURPOSE: profile_purpose,
        ATTR_PROFILE_KIND: profile_kind,
    }

    if is_ocpp21 or profile_kind == "Dynamic":
        if setpoint_value is not None:
            data[ATTR_SETPOINT] = float(setpoint_value)
        if discharge_limit_value is not None:
            data[ATTR_DISCHARGE_LIMIT] = float(discharge_limit_value)
        if operation_mode:
            data[ATTR_OPERATION_MODE] = operation_mode

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

    # TxProfile is transaction-bound and most stations expect Absolute kind with startSchedule.
    if profile_purpose == "TxProfile":
        data[ATTR_PROFILE_KIND] = "Absolute"

    return data


async def async_push_profile_update(
    hass: HomeAssistant,
    coordinator: CitrineCoordinator,
    entry: ConfigEntry,
    station_id: str,
) -> None:
    """Push updated profile preferences to Citrine via service call."""
    coordinator.mark_profile_push_started(station_id)
    data = build_profile_service_data(coordinator, entry, station_id)
    try:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_CHARGING_PROFILE,
            data,
            blocking=True,
        )
    except Exception as err:
        coordinator.mark_profile_push_failed(station_id, str(err))
        raise
    coordinator.mark_profile_push_succeeded(station_id)


def _station_record(coordinator: CitrineCoordinator, station_id: str) -> dict[str, Any]:
    stations = coordinator.data.get("stations", []) if coordinator.data else []
    for station in stations:
        if str(station.get("id")) == str(station_id):
            return station
    return {"id": station_id}
