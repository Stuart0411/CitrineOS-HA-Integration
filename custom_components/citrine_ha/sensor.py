"""Sensor platform for discovered charging stations."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

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
            entities.append(CitrineStationProtocolSensor(coordinator, entry, station))
            entities.append(CitrineStationConnectorCountSensor(coordinator, entry, station))
            entities.append(CitrineStationActiveSessionSensor(coordinator, entry, station))
            entities.append(CitrineStationHeartbeatAgeSensor(coordinator, entry, station))
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
        capabilities = self.coordinator.get_station_capabilities(self._station_id)
        return {
            "protocol": station.get("protocol"),
            "normalized_protocol": self.coordinator.get_station_protocol(
                self._station_id,
                str(station.get("protocol") or ""),
            ),
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
            "capabilities": capabilities,
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


class CitrineStationProtocolSensor(CoordinatorEntity[CitrineCoordinator], SensorEntity):
    """Diagnostic sensor for station protocol."""

    _attr_icon = "mdi:api"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: CitrineCoordinator,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._station_id = str(station["id"])
        self._attr_unique_id = f"{entry.entry_id}_{self._station_id}_protocol"
        self._attr_name = f"{self._station_id} Protocol"

    @property
    def native_value(self) -> str:
        station = self._station()
        protocol = station.get("protocol")
        return str(protocol) if protocol else "unknown"

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._entry, self._station())

    def _station(self) -> dict[str, Any]:
        for station in self.coordinator.data.get("stations", []):
            if str(station.get("id")) == self._station_id:
                return station
        return {"id": self._station_id}


class CitrineStationConnectorCountSensor(CoordinatorEntity[CitrineCoordinator], SensorEntity):
    """Diagnostic sensor for number of connectors discovered."""

    _attr_icon = "mdi:ev-plug-type2"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: CitrineCoordinator,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._station_id = str(station["id"])
        self._attr_unique_id = f"{entry.entry_id}_{self._station_id}_connector_count"
        self._attr_name = f"{self._station_id} Connector Count"

    @property
    def native_value(self) -> int:
        station = self._station()
        return len(station.get("connectors", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        station = self._station()
        connectors = station.get("connectors", [])
        available = [c for c in connectors if str(c.get("status", "")).lower() == "available"]
        occupied = [
            c
            for c in connectors
            if str(c.get("status", "")).lower() in {"occupied", "charging"}
        ]
        faults = [
            c
            for c in connectors
            if str(c.get("status", "")).lower() in {"faulted", "unavailable"}
        ]
        return {
            "available_connectors": len(available),
            "occupied_connectors": len(occupied),
            "faulted_or_unavailable_connectors": len(faults),
        }

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._entry, self._station())

    def _station(self) -> dict[str, Any]:
        for station in self.coordinator.data.get("stations", []):
            if str(station.get("id")) == self._station_id:
                return station
        return {"id": self._station_id}


class CitrineStationActiveSessionSensor(CoordinatorEntity[CitrineCoordinator], SensorEntity):
    """Diagnostic sensor for active session state."""

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
        self._attr_unique_id = f"{entry.entry_id}_{self._station_id}_session"
        self._attr_name = f"{self._station_id} Session State"

    @property
    def native_value(self) -> str:
        station = self._station()
        if station.get("activeTransactionId") is not None:
            return "active"
        return "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        station = self._station()
        return {
            "active_transaction_id": station.get("activeTransactionId"),
            "current_transaction_id": station.get("currentTransactionId"),
            "previous_transaction_id": station.get("previousTransactionId"),
            "next_remote_start_id": station.get("nextRemoteStartId"),
        }

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._entry, self._station())

    def _station(self) -> dict[str, Any]:
        for station in self.coordinator.data.get("stations", []):
            if str(station.get("id")) == self._station_id:
                return station
        return {"id": self._station_id}


class CitrineStationHeartbeatAgeSensor(CoordinatorEntity[CitrineCoordinator], SensorEntity):
    """Diagnostic sensor for age of latest OCPP message in seconds."""

    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "s"

    def __init__(
        self,
        coordinator: CitrineCoordinator,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._station_id = str(station["id"])
        self._attr_unique_id = f"{entry.entry_id}_{self._station_id}_heartbeat_age"
        self._attr_name = f"{self._station_id} OCPP Heartbeat Age"

    @property
    def native_value(self) -> int | None:
        station = self._station()
        value = station.get("latestOcppMessageTimestamp") or station.get("updatedAt")
        if not value:
            return None

        parsed = dt_util.parse_datetime(str(value))
        if parsed is None:
            return None

        if parsed.tzinfo is None:
            parsed = dt_util.as_utc(parsed)
        now = dt_util.utcnow()
        age = int((now - parsed).total_seconds())
        return max(age, 0)

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._entry, self._station())

    def _station(self) -> dict[str, Any]:
        for station in self.coordinator.data.get("stations", []):
            if str(station.get("id")) == self._station_id:
                return station
        return {"id": self._station_id}


def _device_info(entry: ConfigEntry, station: dict[str, Any]) -> DeviceInfo:
    tenant = station.get("tenantId", entry.data.get(CONF_TENANT_ID, 1))
    station_id = str(station.get("id", "unknown"))
    return DeviceInfo(
        identifiers={(DOMAIN, f"{tenant}:{station_id}")},
        name=f"Citrine Charger {station_id}",
        manufacturer=station.get("chargePointVendor") or "Unknown",
        model=station.get("chargePointModel") or station.get("protocol"),
        sw_version=station.get("firmwareVersion"),
    )
