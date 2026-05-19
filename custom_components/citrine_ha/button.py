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

from .citrine_api import CitrineApiError, CitrineClient
from .const import (
    CONF_DEFAULT_EVSE_ID,
    CONF_DEFAULT_ID_TAG,
    CONF_TENANT_ID,
    DEFAULT_DEFAULT_EVSE_ID,
    DEFAULT_DEFAULT_ID_TAG,
    DOMAIN,
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
            entities.append(CitrineStartChargingButton(coordinator, client, entry, station))
            entities.append(CitrineStopChargingButton(coordinator, client, entry, station))
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
        coordinator: CitrineCoordinator,
        client: CitrineClient,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
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
        coordinator: CitrineCoordinator,
        client: CitrineClient,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, client, entry, station)
        self._attr_unique_id = f"{entry.entry_id}_{self._station_id}_start"
        self._attr_name = f"{self._station_id} Start Charging"

    async def async_press(self) -> None:
        station = self._station()
        protocol = str(station.get("protocol", "ocpp2.0.1"))

        id_tag = self._entry.options.get(
            CONF_DEFAULT_ID_TAG,
            self._entry.data.get(CONF_DEFAULT_ID_TAG, DEFAULT_DEFAULT_ID_TAG),
        )
        evse_id = self._resolve_start_target(station)
        remote_start_id = self._consume_next_remote_start_id(station)

        try:
            await self._client.request_start_transaction(
                protocol=protocol,
                station_id=self._station_id,
                id_tag=id_tag,
                evse_id=evse_id,
                remote_start_id=remote_start_id,
            )
        except CitrineApiError as err:
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

    @staticmethod
    def _consume_next_remote_start_id(station: dict[str, Any]) -> int | None:
        next_id = station.get("nextRemoteStartId")
        if next_id is None:
            return None
        try:
            value = int(next_id)
        except (TypeError, ValueError):
            return None
        station["nextRemoteStartId"] = value + 1
        return value


class CitrineStopChargingButton(CitrineBaseButton):
    """Stop charging for discovered active transaction id."""

    _attr_icon = "mdi:stop-circle"

    def __init__(
        self,
        coordinator: CitrineCoordinator,
        client: CitrineClient,
        entry: ConfigEntry,
        station: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, client, entry, station)
        self._attr_unique_id = f"{entry.entry_id}_{self._station_id}_stop"
        self._attr_name = f"{self._station_id} Stop Charging"

    async def async_press(self) -> None:
        station = self._station()
        protocol = str(station.get("protocol", "ocpp2.0.1"))

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
            await self._client.request_stop_transaction(
                protocol=protocol,
                station_id=self._station_id,
                transaction_id=str(transaction_id),
            )
        except CitrineApiError as err:
            raise HomeAssistantError(f"Stop command failed: {err}") from err
