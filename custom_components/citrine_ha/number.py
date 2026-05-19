"""Number entities for per-station load limits."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .citrine_api import CitrineApiError, CitrineClient
from .const import CONF_TENANT_ID, DOMAIN
from .coordinator import CitrineCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: CitrineCoordinator = data["coordinator"]
    client: CitrineClient = data["client"]

    known_ids: set[str] = set()

    def _build_entities() -> list[CitrineStationLimitNumber]:
        entities: list[CitrineStationLimitNumber] = []
        for station in coordinator.data.get("stations", []):
            station_id = station.get("id")
            if not station_id or station_id in known_ids:
                continue
            known_ids.add(station_id)
            entities.append(CitrineStationLimitNumber(coordinator, client, entry, station))
        return entities

    async_add_entities(_build_entities())

    def _async_handle_update() -> None:
        new_entities = _build_entities()
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_handle_update))


class CitrineStationLimitNumber(CoordinatorEntity[CitrineCoordinator], NumberEntity):
    """Writable number for station max charging limit."""

    _attr_icon = "mdi:transmission-tower-export"
    _attr_native_min_value = 0
    _attr_native_max_value = 500000
    _attr_native_step = 100
    _attr_mode = "box"
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self,
        coordinator: CitrineCoordinator,
        client: CitrineClient,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._client = client
        self._entry = entry
        self._station_id = str(station["id"])
        self._attr_unique_id = f"{entry.entry_id}_{self._station_id}_limit"
        self._attr_name = f"{self._station_id} Max Limit"
        self._value = 0.0

    @property
    def native_value(self) -> float:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        station = self._station()
        protocol = str(station.get("protocol", "ocpp2.0.1"))
        try:
            await self._client.set_station_limit(
                protocol=protocol,
                station_id=self._station_id,
                limit=value,
                unit="W",
                evse_id=0,
                duration=300,
            )
        except CitrineApiError as err:
            raise ValueError(f"Failed to apply station limit: {err}") from err

        self._value = value
        self.async_write_ha_state()

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
