"""Sensor platform for discovered charging stations."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_TENANT_ID, DOMAIN
from .coordinator import CitrineCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from coordinator data."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: CitrineCoordinator = data["coordinator"]

    known_ids: set[str] = set()

    def _build_entities() -> list[SensorEntity]:
        entities: list[SensorEntity] = []
        for station in coordinator.data.get("stations", []):
            station_id = station.get("id")
            if not station_id or station_id in known_ids:
                continue
            known_ids.add(station_id)
            entities.append(CitrineStationOnlineSensor(coordinator, entry, station))
            entities.append(CitrineStationTransactionSensor(coordinator, entry, station))
        return entities

    async_add_entities(_build_entities())

    def _async_handle_update() -> None:
        new_entities = _build_entities()
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_handle_update))


class CitrineStationOnlineSensor(CoordinatorEntity[CitrineCoordinator], SensorEntity):
    """Online state sensor for one station."""

    _attr_icon = "mdi:ev-station"

    def __init__(
        self,
        coordinator: CitrineCoordinator,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._station_id = str(station["id"])
        self._attr_unique_id = f"{entry.entry_id}_{self._station_id}_online"
        self._attr_name = f"{self._station_id} Online"

    @property
    def native_value(self) -> str:
        station = self._station()
        return "online" if bool(station.get("isOnline")) else "offline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        station = self._station()
        return {
            "protocol": station.get("protocol"),
            "vendor": station.get("chargePointVendor"),
            "model": station.get("chargePointModel"),
            "serial": station.get("chargePointSerialNumber"),
            "latest_ocpp_message": station.get("latestOcppMessageTimestamp"),
            "updated_at": station.get("updatedAt"),
            "default_evse_id": station.get("defaultEvseId"),
            "active_transaction_id": station.get("activeTransactionId"),
            "previous_transaction_id": station.get("previousTransactionId"),
            "next_remote_start_id": station.get("nextRemoteStartId"),
            "connectors": station.get("connectors", []),
        }

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


class CitrineStationTransactionSensor(CoordinatorEntity[CitrineCoordinator], SensorEntity):
    """Diagnostics sensor for current station transaction mapping."""

    _attr_icon = "mdi:identifier"

    def __init__(
        self,
        coordinator: CitrineCoordinator,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._station_id = str(station["id"])
        self._attr_unique_id = f"{entry.entry_id}_{self._station_id}_transaction"
        self._attr_name = f"{self._station_id} Transaction ID"

    @property
    def native_value(self) -> str | None:
        station = self._station()
        for key in (
            "activeTransactionId",
            "currentTransactionId",
            "transactionId",
            "previousTransactionId",
        ):
            value = station.get(key)
            if value is not None:
                return str(value)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        station = self._station()
        return {
            "active_transaction_id": station.get("activeTransactionId"),
            "current_transaction_id": station.get("currentTransactionId"),
            "previous_transaction_id": station.get("previousTransactionId"),
            "next_remote_start_id": station.get("nextRemoteStartId"),
            "default_evse_id": station.get("defaultEvseId"),
            "protocol": station.get("protocol"),
        }

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
