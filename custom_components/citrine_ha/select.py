"""Select entities for per-station charging profile preferences."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_TENANT_ID, DOMAIN
from .coordinator import CitrineCoordinator
from .profile_controls import async_push_profile_update


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
            capabilities = coordinator.get_station_capabilities(str(station_id))
            entities.append(CitrineStationProfileUnitSelect(coordinator, entry, station))
            entities.append(CitrineStationProfilePurposeSelect(coordinator, entry, station))
            entities.append(CitrineStationProfileKindSelect(coordinator, entry, station))
            entities.append(CitrineStationProfileEvseSelect(coordinator, entry, station))
            entities.append(CitrineStationProfileSignModeSelect(coordinator, entry, station))
            entities.append(CitrineStationProfileTxModeSelect(coordinator, entry, station))
            if bool(capabilities.get("supports_dynamic_profiles", False)):
                entities.append(CitrineStationProfileOperationModeSelect(coordinator, entry, station))
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

    @property
    def available(self) -> bool:
        # Profile controls are writable preferences and should remain available.
        return True

    async def async_select_option(self, option: str) -> None:
        self.coordinator.update_station_profile_preferences(self._station_id, **{self._key: option})
        prefs = self.coordinator.get_station_profile_preferences(self._station_id)
        profile_kind = str(prefs.get("profile_kind", "")).strip().capitalize()
        is_dynamic_live = bool(prefs.get("dynamic_session_active", False)) or profile_kind == "Dynamic"
        if is_dynamic_live and self._key in {
            "unit",
            "profile_purpose",
            "profile_kind",
            "operation_mode",
            "evse_id",
        }:
            await self._async_push_profile_update()
        self.async_write_ha_state()

    async def _async_push_profile_update(self) -> None:
        try:
            await async_push_profile_update(
                self.hass,
                self.coordinator,
                self._entry,
                self._station_id,
            )
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Failed to push profile update: {err}") from err

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


class CitrineStationProfileKindSelect(CitrineProfileSelectBase):
    """Select charging profile kind (Absolute or Relative)."""

    _attr_icon = "mdi:vector-polyline"

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
            key="profile_kind",
            name_suffix="Profile Kind",
            unique_suffix="profile_kind",
        )

    @property
    def options(self) -> list[str]:
        capabilities = self.coordinator.get_station_capabilities(self._station_id)
        options = capabilities.get("supported_profile_kinds", ["Absolute", "Relative"])
        normalized = [str(option).capitalize() for option in options]
        # Keep options stable and unique in case capabilities contain duplicates.
        return list(dict.fromkeys(normalized))


class CitrineStationProfileEvseSelect(CitrineProfileSelectBase):
    """Select EVSE ID from discovered connectors for this station."""

    _attr_icon = "mdi:ev-plug-type2"

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
            key="evse_id",
            name_suffix="Profile EVSE",
            unique_suffix="profile_evse_select",
        )

    @property
    def options(self) -> list[str]:
        station = self._station()
        values: set[int] = set()
        for connector in station.get("connectors", []):
            candidate = connector.get("evseId") or connector.get("connectorId") or connector.get("id")
            if candidate is None:
                continue
            try:
                parsed = int(candidate)
            except (TypeError, ValueError):
                continue
            if parsed >= 1:
                values.add(parsed)

        default_evse = station.get("defaultEvseId")
        try:
            if default_evse is not None and int(default_evse) >= 1:
                values.add(int(default_evse))
        except (TypeError, ValueError):
            pass

        if not values:
            values.add(1)

        return [str(item) for item in sorted(values)]

    @property
    def current_option(self) -> str:
        prefs = self.coordinator.get_station_profile_preferences(self._station_id)
        try:
            value = int(prefs.get("evse_id", 1))
        except (TypeError, ValueError):
            value = 1
        as_str = str(value)
        return as_str if as_str in self.options else self.options[0]

    async def async_select_option(self, option: str) -> None:
        try:
            parsed = int(option)
        except (TypeError, ValueError):
            raise HomeAssistantError("Invalid EVSE selection")
        if parsed < 1:
            raise HomeAssistantError("EVSE selection must be >= 1")
        await super().async_select_option(str(parsed))


class CitrineStationProfileOperationModeSelect(CitrineProfileSelectBase):
    """Select dynamic profile operation mode for OCPP 2.1 stations."""

    _attr_icon = "mdi:swap-horizontal-bold"

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
            key="operation_mode",
            name_suffix="Profile Operation Mode",
            unique_suffix="profile_operation_mode",
        )

    @property
    def options(self) -> list[str]:
        capabilities = self.coordinator.get_station_capabilities(self._station_id)
        return [
            str(option)
            for option in capabilities.get(
                "supported_operation_modes",
                ["ChargingOnly"],
            )
        ]


class CitrineStationProfileSignModeSelect(CitrineProfileSelectBase):
    """Select profile sign compatibility behavior for charger-specific quirks."""

    _attr_icon = "mdi:plus-minus-variant"

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
            key="profile_sign_mode",
            name_suffix="Profile Sign Mode",
            unique_suffix="profile_sign_mode",
        )

    @property
    def options(self) -> list[str]:
        return ["normal", "invert_negative"]


class CitrineStationProfileTxModeSelect(CitrineProfileSelectBase):
    """Select TxProfile compatibility policy for this station."""

    _attr_icon = "mdi:shield-sync"

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
            key="profile_tx_mode",
            name_suffix="Profile Tx Mode",
            unique_suffix="profile_tx_mode",
        )

    @property
    def options(self) -> list[str]:
        return ["safe_fallback", "strict_txprofile"]
