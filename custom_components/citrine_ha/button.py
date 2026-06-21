"""Button entities for start/stop charging actions."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .citrine_api import CitrineApiError, CitrineClient
from .const import (
    ATTR_DURATION,
    ATTR_ENTRY_ID,
    ATTR_EVSE_ID,
    ATTR_ID_TAG,
    ATTR_LIMIT,
    ATTR_PROFILE_ID,
    ATTR_PROFILE_PURPOSE,
    ATTR_PROTOCOL,
    ATTR_STACK_LEVEL,
    ATTR_STATION_ID,
    ATTR_TRANSACTION_ID,
    ATTR_UNIT,
    CONF_DEFAULT_EVSE_ID,
    CONF_DEFAULT_ID_TAG,
    CONF_TENANT_ID,
    DEFAULT_DEFAULT_EVSE_ID,
    DEFAULT_DEFAULT_ID_TAG,
    DOMAIN,
    SERVICE_START_CHARGING,
    SERVICE_STOP_CHARGING,
)
from .coordinator import CitrineCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: CitrineCoordinator = data["coordinator"]
    client: CitrineClient = data["client"]

    known_ids: set[str] = set()

    def _build_entities() -> list[ButtonEntity]:
        entities: list[ButtonEntity] = []
        for station in coordinator.data.get("stations", []):
            station_id = station.get("id")
            if not station_id or station_id in known_ids:
                continue
            known_ids.add(station_id)
            entities.append(CitrineStartChargingButton(hass, coordinator, client, entry, station))
            entities.append(CitrineStopChargingButton(hass, coordinator, client, entry, station))
            entities.append(CitrineApplyChargingProfileButton(hass, coordinator, client, entry, station))
            entities.append(CitrineClearChargingProfileButton(hass, coordinator, client, entry, station))
        return entities

    async_add_entities(_build_entities())

    def _async_handle_update() -> None:
        new_entities = _build_entities()
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_handle_update))


class CitrineBaseButton(CoordinatorEntity[CitrineCoordinator], ButtonEntity):
    """Shared station device mapping for button entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CitrineCoordinator,
        client: CitrineClient,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._hass_instance = hass
        self._client = client
        self._entry = entry
        self._station_id = str(station["id"])

    @property
    def device_info(self) -> DeviceInfo:
        station = self._station()
        tenant = station.get("tenantId", self._entry.data.get(CONF_TENANT_ID, 1))
        station_id = str(station.get("id", self._station_id))
        return DeviceInfo(
            identifiers={(DOMAIN, f"{tenant}:{station_id}")},
            name=f"Citrine Charger {station_id}",
            manufacturer=station.get("chargePointVendor") or "Unknown",
            model=station.get("chargePointModel") or station.get("protocol"),
            sw_version=station.get("firmwareVersion"),
        )

    def _station(self) -> dict[str, Any]:
        for station in self.coordinator.data.get("stations", []):
            if str(station.get("id")) == self._station_id:
                return station
        return {"id": self._station_id}


class CitrineStartChargingButton(CitrineBaseButton):
    """Start charging using default id tag and EVSE."""

    _attr_icon = "mdi:play-circle"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CitrineCoordinator,
        client: CitrineClient,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(hass, coordinator, client, entry, station)
        self._attr_unique_id = f"{entry.entry_id}_{self._station_id}_start"
        self._attr_name = f"{self._station_id} Start Charging"

    async def async_press(self) -> None:
        station = self._station()
        protocol = self._client.normalize_protocol(station.get("protocol"))

        id_tag = self._entry.options.get(
            CONF_DEFAULT_ID_TAG,
            self._entry.data.get(CONF_DEFAULT_ID_TAG, DEFAULT_DEFAULT_ID_TAG),
        )
        evse_id = self._resolve_start_target(station)

        try:
            await self._hass_instance.services.async_call(
                DOMAIN,
                SERVICE_START_CHARGING,
                {
                    ATTR_ENTRY_ID: self._entry.entry_id,
                    ATTR_STATION_ID: self._station_id,
                    ATTR_PROTOCOL: protocol,
                    ATTR_ID_TAG: id_tag,
                    ATTR_EVSE_ID: evse_id,
                },
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Start command failed: {err}") from err

    def _resolve_start_target(self, station: dict[str, Any]) -> int:
        configured_evse_id = self._entry.options.get(
            CONF_DEFAULT_EVSE_ID,
            self._entry.data.get(CONF_DEFAULT_EVSE_ID, DEFAULT_DEFAULT_EVSE_ID),
        )
        if configured_evse_id is not None:
            try:
                return int(configured_evse_id)
            except (TypeError, ValueError):
                pass

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

        return int(
            self._entry.options.get(
                CONF_DEFAULT_EVSE_ID,
                self._entry.data.get(CONF_DEFAULT_EVSE_ID, DEFAULT_DEFAULT_EVSE_ID),
            )
        )

class CitrineStopChargingButton(CitrineBaseButton):
    """Stop charging for discovered active transaction id."""

    _attr_icon = "mdi:stop-circle"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CitrineCoordinator,
        client: CitrineClient,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(hass, coordinator, client, entry, station)
        self._attr_unique_id = f"{entry.entry_id}_{self._station_id}_stop"
        self._attr_name = f"{self._station_id} Stop Charging"

    async def async_press(self) -> None:
        station = self._station()
        protocol = self._client.normalize_protocol(station.get("protocol"))

        transaction_id = (
            station.get("activeTransactionId")
            or station.get("currentTransactionId")
            or station.get("transactionId")
            or station.get("previousTransactionId")
        )
        if transaction_id is None:
            raise HomeAssistantError(
                "No active transaction id found for station. Use the stop_charging service with transaction_id."
            )

        try:
            await self._hass_instance.services.async_call(
                DOMAIN,
                SERVICE_STOP_CHARGING,
                {
                    ATTR_ENTRY_ID: self._entry.entry_id,
                    ATTR_STATION_ID: self._station_id,
                    ATTR_PROTOCOL: protocol,
                    ATTR_TRANSACTION_ID: str(transaction_id),
                },
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Stop command failed: {err}") from err


class CitrineApplyChargingProfileButton(CitrineBaseButton):
    """Apply station charging profile from profile control entities."""

    _attr_icon = "mdi:chart-timeline-variant-shimmer"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CitrineCoordinator,
        client: CitrineClient,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(hass, coordinator, client, entry, station)
        self._attr_unique_id = f"{entry.entry_id}_{self._station_id}_apply_profile"
        self._attr_name = f"{self._station_id} Apply Charging Profile"

    async def async_press(self) -> None:
        station = self._station()
        protocol = self._client.normalize_protocol(
            self.coordinator.get_station_protocol(self._station_id, str(station.get("protocol", "")))
        )
        prefs = self.coordinator.get_station_profile_preferences(self._station_id)
        capabilities = self.coordinator.get_station_capabilities(self._station_id)

        requested_unit = str(prefs.get("unit", "W")).upper()
        allowed_units = [str(unit).upper() for unit in capabilities.get("allowed_units", [])]
        if allowed_units and requested_unit not in allowed_units:
            requested_unit = str(capabilities.get("preferred_unit", allowed_units[0])).upper()

        requested_purpose = str(
            prefs.get(
                "profile_purpose",
                capabilities.get("default_profile_purpose", "TxDefaultProfile"),
            )
        )
        requested_kind = str(
            prefs.get(
                "profile_kind",
                capabilities.get("default_profile_kind", "Absolute"),
            )
        )
        supported_purposes = [str(item) for item in capabilities.get("supported_profile_purposes", [])]
        supported_kinds = [str(item) for item in capabilities.get("supported_profile_kinds", [])]
        if supported_purposes and requested_purpose not in supported_purposes:
            requested_purpose = str(capabilities.get("default_profile_purpose", supported_purposes[0]))
        if supported_kinds and requested_kind not in supported_kinds:
            requested_kind = str(capabilities.get("default_profile_kind", supported_kinds[0]))

        transaction_id = (
            station.get("activeTransactionId")
            or station.get("currentTransactionId")
            or station.get("transactionId")
            or station.get("previousTransactionId")
        )
        tx_mode = str(prefs.get("profile_tx_mode", "safe_fallback"))
        if requested_purpose == "TxProfile" and transaction_id is None:
            if tx_mode == "strict_txprofile":
                raise HomeAssistantError(
                    "TxProfile strict mode requires an active transaction id, but no transaction is available"
                )
            requested_purpose = str(capabilities.get("default_profile_purpose", "TxDefaultProfile"))
            if requested_purpose == "TxProfile":
                requested_purpose = next(
                    (item for item in supported_purposes if item != "TxProfile"),
                    "TxDefaultProfile",
                )

        purpose_key = requested_purpose.lower()
        tx_for_command = str(transaction_id) if (transaction_id is not None and purpose_key == "txprofile") else None

        limit_value = float(prefs.get("limit", 7000.0))
        sign_mode = str(prefs.get("profile_sign_mode", "normal"))
        if sign_mode == "invert_negative" and limit_value < 0:
            limit_value = abs(limit_value)

        supports_bidirectional = bool(capabilities.get("supports_bidirectional_power_transfer", False))
        if limit_value < 0 and not supports_bidirectional:
            raise HomeAssistantError(
                f"Station {self._station_id} does not advertise bidirectional profile support"
            )
        min_profile_limit = capabilities.get("min_profile_limit")
        max_profile_limit = capabilities.get("max_profile_limit")
        if min_profile_limit is not None:
            limit_value = max(float(min_profile_limit), limit_value)
        if max_profile_limit is not None:
            limit_value = min(float(max_profile_limit), limit_value)

        profile_id = prefs.get("profile_id")
        evse_id = int(prefs.get("evse_id", 0))
        duration = int(prefs.get("duration", 300))
        stack_level = int(prefs.get("stack_level", 1))

        try:
            _LOGGER.warning(
                "Apply profile requested: station=%s protocol=%s evse=%s limit=%s unit=%s purpose=%s kind=%s tx=%s sign_mode=%s tx_mode=%s",
                self._station_id,
                protocol,
                evse_id,
                limit_value,
                requested_unit,
                requested_purpose,
                requested_kind,
                tx_for_command,
                sign_mode,
                tx_mode,
            )

            await self._client.set_charging_profile(
                protocol=protocol,
                station_id=self._station_id,
                limit=limit_value,
                unit=requested_unit,
                evse_id=evse_id,
                duration=duration,
                stack_level=stack_level,
                profile_id=int(profile_id) if profile_id is not None else None,
                profile_purpose=requested_purpose,
                profile_kind=requested_kind,
                transaction_id=tx_for_command,
                txprofile_compatibility_fallback=(tx_mode != "strict_txprofile"),
            )
            await self.coordinator.async_request_refresh()
        except CitrineApiError as err:
            raise HomeAssistantError(f"Apply profile command failed: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Apply profile command failed: {err}") from err


class CitrineClearChargingProfileButton(CitrineBaseButton):
    """Clear station charging profile from profile control entities."""

    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CitrineCoordinator,
        client: CitrineClient,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(hass, coordinator, client, entry, station)
        self._attr_unique_id = f"{entry.entry_id}_{self._station_id}_clear_profile"
        self._attr_name = f"{self._station_id} Clear Charging Profile"

    async def async_press(self) -> None:
        station = self._station()
        protocol = self._client.normalize_protocol(
            self.coordinator.get_station_protocol(self._station_id, str(station.get("protocol", "")))
        )
        prefs = self.coordinator.get_station_profile_preferences(self._station_id)
        capabilities = self.coordinator.get_station_capabilities(self._station_id)
        supported_purposes = [str(item) for item in capabilities.get("supported_profile_purposes", [])]
        requested_purpose = str(
            prefs.get(
                "profile_purpose",
                capabilities.get("default_profile_purpose", "TxDefaultProfile"),
            )
        )
        if supported_purposes and requested_purpose not in supported_purposes:
            requested_purpose = str(capabilities.get("default_profile_purpose", supported_purposes[0]))

        profile_id = prefs.get("profile_id")
        evse_id = int(prefs.get("evse_id", 0))
        stack_level = int(prefs.get("stack_level", 1))

        try:
            _LOGGER.warning(
                "Clear profile requested: station=%s protocol=%s evse=%s purpose=%s profile_id=%s",
                self._station_id,
                protocol,
                evse_id,
                requested_purpose,
                profile_id,
            )

            await self._client.clear_charging_profile(
                protocol=protocol,
                station_id=self._station_id,
                evse_id=evse_id,
                profile_id=int(profile_id) if profile_id is not None else None,
                stack_level=stack_level,
                profile_purpose=requested_purpose,
            )
            await self.coordinator.async_request_refresh()
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Clear profile command failed: {err}") from err
