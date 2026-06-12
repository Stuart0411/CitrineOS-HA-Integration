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
from .const import (
    ATTR_DURATION,
    ATTR_ENTRY_ID,
    ATTR_EVSE_ID,
    ATTR_LIMIT,
    ATTR_PROFILE_ID,
    ATTR_PROTOCOL,
    ATTR_STACK_LEVEL,
    ATTR_STATION_ID,
    ATTR_UNIT,
    DEFAULT_PROFILE_DURATION,
    DEFAULT_PROFILE_LIMIT,
    DEFAULT_PROFILE_STACK_LEVEL,
    DEFAULT_PROFILE_UNIT,
    SERVICE_SET_STATION_LIMIT,
)
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
            entities.append(CitrineStationProfileLimitNumber(coordinator, entry, station))
            entities.append(CitrineStationProfileDurationNumber(coordinator, entry, station))
            entities.append(CitrineStationProfileEvseNumber(coordinator, entry, station))
            entities.append(CitrineStationProfileStackLevelNumber(coordinator, entry, station))
            entities.append(CitrineStationProfileIdNumber(coordinator, entry, station))
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
        protocol = self._client.normalize_protocol(
            self.coordinator.get_station_protocol(self._station_id, station.get("protocol"))
        )
        try:
            await self.hass.services.async_call(
                DOMAIN,
                SERVICE_SET_STATION_LIMIT,
                {
                    ATTR_ENTRY_ID: self._entry.entry_id,
                    ATTR_STATION_ID: self._station_id,
                    ATTR_PROTOCOL: protocol,
                    ATTR_LIMIT: float(value),
                    ATTR_UNIT: "W",
                    ATTR_EVSE_ID: 0,
                    ATTR_DURATION: 300,
                },
                blocking=True,
            )
        except CitrineApiError as err:
            raise ValueError(f"Failed to apply station limit: {err}") from err
        except Exception as err:  # noqa: BLE001
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


class CitrineProfilePreferenceNumber(CoordinatorEntity[CitrineCoordinator], NumberEntity):
    """Base writable number for cached per-station profile preferences."""

    _attr_mode = "box"

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
    def native_value(self) -> float:
        prefs = self.coordinator.get_station_profile_preferences(self._station_id)
        value = prefs.get(self._key)
        if value is None:
            return 0.0
        return float(value)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.update_station_profile_preferences(self._station_id, **{self._key: value})
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


class CitrineStationProfileLimitNumber(CitrineProfilePreferenceNumber):
    _attr_icon = "mdi:gauge"
    _attr_native_step = 100
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    @property
    def native_min_value(self) -> float:
        capabilities = self.coordinator.get_station_capabilities(self._station_id)
        return float(capabilities.get("min_profile_limit", -500000.0))

    @property
    def native_max_value(self) -> float:
        capabilities = self.coordinator.get_station_capabilities(self._station_id)
        return float(capabilities.get("max_profile_limit", 500000.0))

    def __init__(self, coordinator: CitrineCoordinator, entry: ConfigEntry, station: dict[str, Any]) -> None:
        super().__init__(
            coordinator,
            entry,
            station,
            key="limit",
            name_suffix="Profile Limit",
            unique_suffix="profile_limit",
        )
        self.coordinator.update_station_profile_preferences(
            self._station_id,
            limit=self.coordinator.get_station_profile_preferences(self._station_id).get("limit", DEFAULT_PROFILE_LIMIT),
        )


class CitrineStationProfileDurationNumber(CitrineProfilePreferenceNumber):
    _attr_icon = "mdi:timer-cog"
    _attr_native_min_value = 0
    _attr_native_max_value = 86400
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "s"

    def __init__(self, coordinator: CitrineCoordinator, entry: ConfigEntry, station: dict[str, Any]) -> None:
        super().__init__(
            coordinator,
            entry,
            station,
            key="duration",
            name_suffix="Profile Duration",
            unique_suffix="profile_duration",
        )
        self.coordinator.update_station_profile_preferences(
            self._station_id,
            duration=self.coordinator.get_station_profile_preferences(self._station_id).get("duration", DEFAULT_PROFILE_DURATION),
        )


class CitrineStationProfileEvseNumber(CitrineProfilePreferenceNumber):
    _attr_icon = "mdi:ev-plug-type2"
    _attr_native_min_value = 0
    _attr_native_max_value = 999
    _attr_native_step = 1

    def __init__(self, coordinator: CitrineCoordinator, entry: ConfigEntry, station: dict[str, Any]) -> None:
        super().__init__(
            coordinator,
            entry,
            station,
            key="evse_id",
            name_suffix="Profile EVSE ID",
            unique_suffix="profile_evse",
        )


class CitrineStationProfileStackLevelNumber(CitrineProfilePreferenceNumber):
    _attr_icon = "mdi:layers-triple"
    _attr_native_min_value = 0
    _attr_native_max_value = 999
    _attr_native_step = 1

    def __init__(self, coordinator: CitrineCoordinator, entry: ConfigEntry, station: dict[str, Any]) -> None:
        super().__init__(
            coordinator,
            entry,
            station,
            key="stack_level",
            name_suffix="Profile Stack Level",
            unique_suffix="profile_stack",
        )
        self.coordinator.update_station_profile_preferences(
            self._station_id,
            stack_level=self.coordinator.get_station_profile_preferences(self._station_id).get("stack_level", DEFAULT_PROFILE_STACK_LEVEL),
        )


class CitrineStationProfileIdNumber(CitrineProfilePreferenceNumber):
    _attr_icon = "mdi:numeric"
    _attr_native_min_value = 0
    _attr_native_max_value = 999999
    _attr_native_step = 1

    def __init__(self, coordinator: CitrineCoordinator, entry: ConfigEntry, station: dict[str, Any]) -> None:
        super().__init__(
            coordinator,
            entry,
            station,
            key="profile_id",
            name_suffix="Profile ID",
            unique_suffix="profile_id",
        )

    @property
    def native_value(self) -> float:
        prefs = self.coordinator.get_station_profile_preferences(self._station_id)
        value = prefs.get(self._key)
        if value is None:
            return 0.0
        return float(value)

    async def async_set_native_value(self, value: float) -> None:
        # Value 0 means auto-generated profile id on apply.
        self.coordinator.update_station_profile_preferences(
            self._station_id,
            profile_id=None if int(value) == 0 else int(value),
        )
        self.async_write_ha_state()
