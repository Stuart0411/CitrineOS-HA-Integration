"""Button entities for start/stop charging actions."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .citrine_api import CitrineClient
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
    SERVICE_CLEAR_CHARGING_PROFILE,
    SERVICE_SET_CHARGING_PROFILE,
    SERVICE_START_CHARGING,
    SERVICE_STOP_CHARGING,
)
from .coordinator import CitrineCoordinator


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

        service_data = {
            ATTR_ENTRY_ID: self._entry.entry_id,
            ATTR_STATION_ID: self._station_id,
            ATTR_PROTOCOL: protocol,
            ATTR_LIMIT: float(prefs.get("limit", 7000.0)),
            ATTR_EVSE_ID: int(prefs.get("evse_id", 0)),
            ATTR_DURATION: int(prefs.get("duration", 300)),
            ATTR_STACK_LEVEL: int(prefs.get("stack_level", 1)),
            ATTR_UNIT: str(prefs.get("unit", "W")),
            ATTR_PROFILE_PURPOSE: str(prefs.get("profile_purpose", "TxProfile")),
        }
        profile_id = prefs.get("profile_id")
        if profile_id is not None:
            service_data[ATTR_PROFILE_ID] = int(profile_id)

        try:
            await self._hass_instance.services.async_call(
                DOMAIN,
                SERVICE_SET_CHARGING_PROFILE,
                service_data,
                blocking=True,
            )
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

        service_data = {
            ATTR_ENTRY_ID: self._entry.entry_id,
            ATTR_STATION_ID: self._station_id,
            ATTR_PROTOCOL: protocol,
            ATTR_EVSE_ID: int(prefs.get("evse_id", 0)),
            ATTR_STACK_LEVEL: int(prefs.get("stack_level", 1)),
            ATTR_PROFILE_PURPOSE: str(prefs.get("profile_purpose", "TxProfile")),
        }
        profile_id = prefs.get("profile_id")
        if profile_id is not None:
            service_data[ATTR_PROFILE_ID] = int(profile_id)

        try:
            await self._hass_instance.services.async_call(
                DOMAIN,
                SERVICE_CLEAR_CHARGING_PROFILE,
                service_data,
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Clear profile command failed: {err}") from err
