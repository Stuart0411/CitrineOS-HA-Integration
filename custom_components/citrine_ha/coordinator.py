"""Coordinator for Citrine charger discovery and state."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_HASURA_QUERY,
    CONF_HASURA_URL,
    CONF_SCAN_INTERVAL,
    CONF_TENANT_ID,
    DEFAULT_HASURA_QUERY,
    DEFAULT_SCAN_INTERVAL,
)
from .hasura_client import HasuraClient, HasuraError

_LOGGER = logging.getLogger(__name__)


class CitrineCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Data coordinator for discovered charging stations."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        hasura_client: HasuraClient | None,
        entry_data: dict[str, Any],
        entry_options: dict[str, Any],
    ) -> None:
        scan_interval = entry_options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name="citrine_ha",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._hasura_client = hasura_client
        self._entry_data = entry_data
        self._entry_options = entry_options

    async def _async_update_data(self) -> dict[str, Any]:
        tenant_id = int(self._entry_data[CONF_TENANT_ID])

        if not self._hasura_client:
            return {"stations": [], "source": "none"}

        query = self._entry_options.get(CONF_HASURA_QUERY) or self._entry_data.get(
            CONF_HASURA_QUERY,
            DEFAULT_HASURA_QUERY,
        )

        active_query = query
        for _attempt in range(4):
            try:
                result = await self._hasura_client.query(
                    active_query,
                    variables={"tenantId": tenant_id},
                )
                break
            except HasuraError as err:
                if "not a valid graphql query" in str(err).lower() and active_query != DEFAULT_HASURA_QUERY:
                    active_query = DEFAULT_HASURA_QUERY
                    continue
                fallback_query = self._query_without_missing_fields(active_query, err)
                if fallback_query == active_query:
                    raise UpdateFailed(f"Hasura discovery failed: {err}") from err
                active_query = fallback_query
        else:
            raise UpdateFailed("Hasura discovery failed after schema fallback retries")

        data = result.get("data", {})
        stations = self._extract_stations(data)
        connectors = self._extract_connectors(data)
        transactions = self._extract_transactions(data)
        merged_stations = self._merge_station_state(
            stations=stations,
            connectors=connectors,
            transactions=transactions,
        )

        return {
            "stations": merged_stations,
            "connectors": connectors,
            "transactions": transactions,
            "source": "hasura",
            "hasura_url": self._entry_data.get(CONF_HASURA_URL),
        }

    @staticmethod
    def _extract_stations(data: dict[str, Any]) -> list[dict[str, Any]]:
        # Default Hasura table naming can vary by configuration.
        for key in ("ChargingStations", "chargingStations", "charging_stations"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _query_without_missing_fields(query: str, error: HasuraError) -> str:
        updated = query
        for item in error.errors:
            message = item.get("message", "")
            match = re.search(r"field '([^']+)' not found in type: '([^']+)'", message)
            if not match:
                continue
            field_name, type_name = match.groups()
            updated = CitrineCoordinator._remove_field_from_block(updated, type_name, field_name)
        return " ".join(updated.split())

    @staticmethod
    def _remove_field_from_block(query: str, type_name: str, field_name: str) -> str:
        pattern = re.compile(
            rf"({re.escape(type_name)}(?:\s*\([^)]*\))?\s*\{{)([^}}]*)(\}})"
        )

        def _replace(match: re.Match[str]) -> str:
            prefix, body, suffix = match.groups()
            fields = [field for field in body.split() if field != field_name]
            return f"{prefix}{' '.join(fields)}{suffix}"

        return pattern.sub(_replace, query, count=1)

    @staticmethod
    def _extract_connectors(data: dict[str, Any]) -> list[dict[str, Any]]:
        for key in (
            "Connectors",
            "connectors",
            "ChargingStationConnectors",
            "chargingStationConnectors",
            "evses",
            "EVSEs",
        ):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_transactions(data: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("Transactions", "transactions", "TransactionEvents", "transactionEvents"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _merge_station_state(
        *,
        stations: list[dict[str, Any]],
        connectors: list[dict[str, Any]],
        transactions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        connectors_by_station: dict[str, list[dict[str, Any]]] = {}
        for connector in connectors:
            station_id = CitrineCoordinator._station_ref(connector)
            if not station_id:
                continue
            connectors_by_station.setdefault(station_id, []).append(connector)

        tx_by_station: dict[str, list[dict[str, Any]]] = {}
        for tx in transactions:
            station_id = CitrineCoordinator._station_ref(tx)
            if not station_id:
                continue
            tx_by_station.setdefault(station_id, []).append(tx)

        merged: list[dict[str, Any]] = []
        for station in stations:
            station_id = str(station.get("id", ""))
            station_connectors = connectors_by_station.get(station_id, [])
            station_transactions = tx_by_station.get(station_id, [])

            ordered_transactions = sorted(
                station_transactions,
                key=lambda tx: str(
                    tx.get("updatedAt")
                    or tx.get("stoppedAt")
                    or tx.get("endedAt")
                    or tx.get("startedAt")
                    or ""
                ),
                reverse=True,
            )

            active_tx = next(
                (
                    tx
                    for tx in ordered_transactions
                    if tx.get("isActive") is True
                    or tx.get("active") is True
                    or (
                        tx.get("stoppedAt") is None
                        and tx.get("endedAt") is None
                        and tx.get("startedAt") is not None
                    )
                ),
                None,
            )

            current_tx_id = CitrineCoordinator._transaction_ref(active_tx)
            previous_tx_id = None
            if ordered_transactions:
                previous_tx_id = CitrineCoordinator._transaction_ref(ordered_transactions[0])

            numeric_tx_ids: list[int] = []
            for tx in ordered_transactions:
                tx_id = CitrineCoordinator._transaction_ref(tx)
                if tx_id is None:
                    continue
                try:
                    numeric_tx_ids.append(int(tx_id))
                except (TypeError, ValueError):
                    continue
            next_remote_start_id = (max(numeric_tx_ids) + 1) if numeric_tx_ids else 1

            normalized_connectors = sorted(
                [
                    {
                        "id": conn.get("id"),
                        "connectorId": conn.get("connectorId") or conn.get("id"),
                        "evseId": conn.get("evseId") or conn.get("connectorId") or conn.get("id"),
                        "status": conn.get("status"),
                        "isOnline": conn.get("isOnline"),
                        "updatedAt": conn.get("updatedAt"),
                    }
                    for conn in station_connectors
                ],
                key=lambda conn: str(conn.get("evseId") or conn.get("connectorId") or ""),
            )

            default_evse_id = 1
            for conn in normalized_connectors:
                candidate = conn.get("evseId")
                try:
                    if candidate is not None:
                        default_evse_id = int(candidate)
                        break
                except (TypeError, ValueError):
                    continue

            merged.append(
                {
                    **station,
                    "connectors": normalized_connectors,
                    "activeTransactionId": current_tx_id,
                    "currentTransactionId": current_tx_id,
                    "previousTransactionId": previous_tx_id,
                    "defaultEvseId": default_evse_id,
                    "nextRemoteStartId": next_remote_start_id,
                }
            )

        return merged

    @staticmethod
    def _station_ref(payload: dict[str, Any] | None) -> str | None:
        if not payload:
            return None
        station_id = payload.get("stationId") or payload.get("chargingStationId") or payload.get("identifier")
        if station_id is None:
            return None
        return str(station_id)

    @staticmethod
    def _transaction_ref(payload: dict[str, Any] | None) -> str | None:
        if not payload:
            return None
        tx_id = payload.get("transactionId") or payload.get("id")
        if tx_id is None:
            return None
        return str(tx_id)
