"""Select entities for per-station charging profile preferences."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
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
    """Set up select entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: CitrineCoordinator = data["coordinator"]

    known_ids: set[str] = set()

    def _build_entities() -> list[SelectEntity]:
        entities: list[SelectEntity] = []
        for station in coordinator.data.get("stations", []):
            station_id = station.get("id")
            if not station_id or station_id in known_ids:
                continue
            known_ids.add(station_id)
            entities.append(CitrineStationProfileUnitSelect(coordinator, entry, station))
            entities.append(CitrineStationProfilePurposeSelect(coordinator, entry, station))
        return entities

    async_add_entities(_build_entities())

    def _async_handle_update() -> None:
        new_entities = _build_entities()
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_handle_update))


class CitrineProfileSelectBase(CoordinatorEntity[CitrineCoordinator], SelectEntity):
    """Base class for profile preference selects."""

    def __init__(
        self,
        coordinator: CitrineCoordinator,
        entry: ConfigEntry,
        station: dict[str, Any],
        *,
        key: str,
        name_suffix: str,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._station_id = str(station["id"])
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{self._station_id}_{unique_suffix}"
        self._attr_name = f"{self._station_id} {name_suffix}"

    @property
    def current_option(self) -> str:
        prefs = self.coordinator.get_station_profile_preferences(self._station_id)
        value = prefs.get(self._key)
        if value is None:
            return self.options[0]
        as_str = str(value)
        return as_str if as_str in self.options else self.options[0]

    async def async_select_option(self, option: str) -> None:
        self.coordinator.update_station_profile_preferences(self._station_id, **{self._key: option})
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


class CitrineStationProfileUnitSelect(CitrineProfileSelectBase):
    """Select charging profile rate unit based on station capabilities."""

    _attr_icon = "mdi:scale"

    def __init__(
        self,
        coordinator: CitrineCoordinator,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            station,
            key="unit",
            name_suffix="Profile Unit",
            unique_suffix="profile_unit",
        )

    @property
    def options(self) -> list[str]:
        capabilities = self.coordinator.get_station_capabilities(self._station_id)
        options = capabilities.get("allowed_units", ["W", "A"])
        return [str(option).upper() for option in options]


class CitrineStationProfilePurposeSelect(CitrineProfileSelectBase):
    """Select charging profile purpose based on station capabilities."""

    _attr_icon = "mdi:shape-outline"

    def __init__(
        self,
        coordinator: CitrineCoordinator,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            station,
            key="profile_purpose",
            name_suffix="Profile Purpose",
            unique_suffix="profile_purpose",
        )

    @property
    def options(self) -> list[str]:
        capabilities = self.coordinator.get_station_capabilities(self._station_id)
        options = capabilities.get(
            "supported_profile_purposes",
            ["TxProfile", "TxDefaultProfile"],
        )
        return [str(option) for option in options]
