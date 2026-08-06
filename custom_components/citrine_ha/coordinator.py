"""Coordinator for Citrine charger discovery and state."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_HASURA_QUERY,
    CONF_HASURA_URL,
    CONF_SCAN_INTERVAL,
    CONF_TENANT_ID,
    DEFAULT_HASURA_QUERY,
    DEFAULT_PROFILE_DURATION,
    DEFAULT_PROFILE_DISCHARGE_LIMIT,
    DEFAULT_PROFILE_KIND,
    DEFAULT_PROFILE_LIMIT,
    DEFAULT_PROFILE_OPERATION_MODE,
    DEFAULT_PROFILE_SETPOINT,
    DEFAULT_PROFILE_PURPOSE,
    DEFAULT_PROFILE_STACK_LEVEL,
    DEFAULT_PROFILE_UNIT,
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
        self._protocol_cache: dict[str, str] = {}
        self._capability_cache: dict[str, dict[str, Any]] = {}
        self._profile_prefs: dict[str, dict[str, Any]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        tenant_id = int(self._entry_data[CONF_TENANT_ID])

        if not self._hasura_client:
            return {"stations": [], "source": "none"}

        query = self._entry_options.get(CONF_HASURA_QUERY) or self._entry_data.get(
            CONF_HASURA_QUERY,
            DEFAULT_HASURA_QUERY,
        )
        query = self._sanitize_discovery_query(query)

        active_query = query
        last_error: HasuraError | None = None
        for _attempt in range(4):
            try:
                result = await self._hasura_client.query(
                    active_query,
                    variables={"tenantId": tenant_id},
                )
                break
            except HasuraError as err:
                last_error = err
                if "not a valid graphql query" in str(err).lower() and active_query != DEFAULT_HASURA_QUERY:
                    active_query = DEFAULT_HASURA_QUERY
                    continue
                fallback_query = self._query_without_missing_fields(active_query, err)
                if fallback_query == active_query:
                    break
                active_query = fallback_query
        else:
            pass

        if "result" not in locals():
            previous_stations: list[dict[str, Any]] = []
            if self.data and isinstance(self.data.get("stations"), list):
                previous_stations = [
                    item for item in self.data.get("stations", []) if isinstance(item, dict)
                ]

            error_message = "Hasura discovery failed"
            if last_error is not None:
                error_message = f"Hasura discovery failed after schema fallback retries: {last_error}"

            _LOGGER.warning(
                "%s; continuing setup with %s cached stations",
                error_message,
                len(previous_stations),
            )

            if previous_stations:
                self._refresh_station_caches(previous_stations)

            return {
                "stations": previous_stations,
                "connectors": [],
                "transactions": [],
                "source": "hasura_error",
                "hasura_url": self._entry_data.get(CONF_HASURA_URL),
                "discovery_error": error_message,
            }

        data = result.get("data", {})
        stations = self._extract_stations(data)
        connectors = self._extract_connectors(data)
        transactions = self._extract_transactions(data)
        merged_stations = self._merge_station_state(
            stations=stations,
            connectors=connectors,
            transactions=transactions,
        )
        if not merged_stations and self.data and isinstance(self.data.get("stations"), list):
            previous_stations = [
                item for item in self.data.get("stations", []) if isinstance(item, dict)
            ]
            if previous_stations:
                _LOGGER.warning(
                    "Discovery returned zero stations; retaining %s previously discovered stations",
                    len(previous_stations),
                )
                merged_stations = previous_stations
        self._refresh_station_caches(merged_stations)

        return {
            "stations": merged_stations,
            "connectors": connectors,
            "transactions": transactions,
            "source": "hasura",
            "hasura_url": self._entry_data.get(CONF_HASURA_URL),
        }

    def get_station_protocol(self, station_id: str, fallback: str | None = None) -> str | None:
        station_key = str(station_id)
        if station_key in self._protocol_cache:
            return self._protocol_cache[station_key]
        return fallback

    def get_station_capabilities(self, station_id: str) -> dict[str, Any]:
        station_key = str(station_id)
        return dict(self._capability_cache.get(station_key, {}))

    def get_station_profile_preferences(self, station_id: str) -> dict[str, Any]:
        station_key = str(station_id)
        defaults = {
            "limit": DEFAULT_PROFILE_LIMIT,
            "setpoint": DEFAULT_PROFILE_SETPOINT,
            "discharge_limit": DEFAULT_PROFILE_DISCHARGE_LIMIT,
            "unit": DEFAULT_PROFILE_UNIT,
            "duration": DEFAULT_PROFILE_DURATION,
            "evse_id": 0,
            "stack_level": DEFAULT_PROFILE_STACK_LEVEL,
            "profile_id": None,
            "profile_purpose": DEFAULT_PROFILE_PURPOSE,
            "profile_kind": DEFAULT_PROFILE_KIND,
            "operation_mode": DEFAULT_PROFILE_OPERATION_MODE,
            "profile_periods": None,
            "profile_sign_mode": "normal",
            "profile_tx_mode": "safe_fallback",
            "dynamic_session_active": False,
            "last_profile_push_status": "idle",
            "last_profile_push_at": None,
            "last_profile_push_error": None,
        }
        if station_key not in self._profile_prefs:
            self._profile_prefs[station_key] = dict(defaults)
        return {**defaults, **self._profile_prefs[station_key]}

    def update_station_profile_preferences(self, station_id: str, **kwargs: Any) -> None:
        station_key = str(station_id)
        prefs = self.get_station_profile_preferences(station_key)
        for key, value in kwargs.items():
            if value is not None:
                prefs[key] = value
        self._profile_prefs[station_key] = prefs

    def mark_profile_push_started(self, station_id: str) -> None:
        self.update_station_profile_preferences(
            station_id,
            last_profile_push_status="pending",
            last_profile_push_error=None,
        )

    def mark_profile_push_succeeded(self, station_id: str) -> None:
        self.update_station_profile_preferences(
            station_id,
            last_profile_push_status="applied",
            last_profile_push_error=None,
            last_profile_push_at=datetime.now(UTC).isoformat(),
        )

    def mark_profile_push_failed(self, station_id: str, error: str) -> None:
        self.update_station_profile_preferences(
            station_id,
            last_profile_push_status="failed",
            last_profile_push_error=str(error)[:300],
            last_profile_push_at=datetime.now(UTC).isoformat(),
        )

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
            match = re.search(r"field ['\"]([^'\"]+)['\"] not found in type: ['\"]([^'\"]+)['\"]", message)
            if not match:
                continue
            field_name, type_name = match.groups()
            updated = CitrineCoordinator._remove_field_from_block(updated, type_name, field_name)
        return " ".join(updated.split())

    @staticmethod
    def _sanitize_discovery_query(query: str) -> str:
        """Pre-clean optional fields that are commonly absent across schemas."""
        sanitized = str(query)
        # Some deployments do not expose Transactions.idToken.
        sanitized = re.sub(r"\bidToken\b", "", sanitized, flags=re.IGNORECASE)
        return " ".join(sanitized.split())

    @staticmethod
    def _remove_field_from_block(query: str, type_name: str, field_name: str) -> str:
        pattern = re.compile(
            rf"({re.escape(type_name)}(?:\s*\([^)]*\))?\s*\{{)([^}}]*)(\}})",
            re.IGNORECASE,
        )

        def _replace(match: re.Match[str]) -> str:
            prefix, body, suffix = match.groups()
            fields = [field for field in body.split() if field != field_name]
            return f"{prefix}{' '.join(fields)}{suffix}"
        updated = pattern.sub(_replace, query, count=1)
        if updated != query:
            return updated

        # Fallback: remove the field token globally when block matching fails due to schema aliasing.
        token_pattern = re.compile(rf"\b{re.escape(field_name)}\b")
        return token_pattern.sub("", query, count=1)

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

    def _refresh_station_caches(self, stations: list[dict[str, Any]]) -> None:
        for station in stations:
            station_id = str(station.get("id", ""))
            if not station_id:
                continue

            protocol = self._normalize_protocol(station.get("protocol"))
            self._protocol_cache[station_id] = protocol

            capabilities = self._derive_capabilities(station, protocol)
            self._capability_cache[station_id] = capabilities

            if station_id not in self._profile_prefs:
                self._profile_prefs[station_id] = {
                    "limit": DEFAULT_PROFILE_LIMIT,
                    "setpoint": DEFAULT_PROFILE_SETPOINT,
                    "discharge_limit": DEFAULT_PROFILE_DISCHARGE_LIMIT,
                    "unit": capabilities.get("preferred_unit", DEFAULT_PROFILE_UNIT),
                    "duration": DEFAULT_PROFILE_DURATION,
                    "evse_id": int(station.get("defaultEvseId") or 0),
                    "stack_level": DEFAULT_PROFILE_STACK_LEVEL,
                    "profile_id": None,
                    "profile_purpose": capabilities.get(
                        "default_profile_purpose",
                        DEFAULT_PROFILE_PURPOSE,
                    ),
                    "profile_kind": capabilities.get("default_profile_kind", DEFAULT_PROFILE_KIND),
                    "operation_mode": capabilities.get(
                        "default_operation_mode",
                        DEFAULT_PROFILE_OPERATION_MODE,
                    ),
                    "profile_periods": None,
                    "profile_sign_mode": "normal",
                    "profile_tx_mode": "safe_fallback",
                    "dynamic_session_active": False,
                    "last_profile_push_status": "idle",
                    "last_profile_push_at": None,
                    "last_profile_push_error": None,
                }
            else:
                # Migrate old defaults that used TxProfile by default, which can fail without a transaction.
                existing = self._profile_prefs[station_id]
                if existing.get("profile_purpose") == "TxProfile":
                    existing["profile_purpose"] = capabilities.get(
                        "default_profile_purpose",
                        existing.get("profile_purpose"),
                    )
                if "dynamic_session_active" not in existing:
                    existing["dynamic_session_active"] = False
                if "last_profile_push_status" not in existing:
                    existing["last_profile_push_status"] = "idle"
                if "last_profile_push_at" not in existing:
                    existing["last_profile_push_at"] = None
                if "last_profile_push_error" not in existing:
                    existing["last_profile_push_error"] = None

    @staticmethod
    def _normalize_protocol(protocol: Any) -> str:
        value = str(protocol or "").strip().lower().replace(" ", "")
        if value in {"1.6", "ocpp1.6", "ocpp16", "ocpp-1.6"}:
            return "ocpp1.6"
        if value in {"2.1", "ocpp2.1", "ocpp21", "ocpp-2.1"}:
            return "ocpp2.1"
        if value in {"2.0", "2.0.1", "ocpp2.0", "ocpp2.0.1", "ocpp201", "ocpp-2.0.1"}:
            return "ocpp2.0.1"
        return "ocpp2.0.1"

    @staticmethod
    def _derive_capabilities(station: dict[str, Any], protocol: str) -> dict[str, Any]:
        connectors = station.get("connectors", [])
        connector_count = len(connectors)

        if protocol == "ocpp1.6":
            return {
                "supports_remote_start": True,
                "supports_remote_stop": True,
                "supports_set_charging_profile": True,
                "supports_clear_charging_profile": True,
                "supports_bidirectional_power_transfer": False,
                "allowed_units": ["A", "W"],
                "preferred_unit": "A",
                "min_profile_limit": 0.0,
                "max_profile_limit": 500000.0,
                "supported_profile_purposes": [
                    "ChargePointMaxProfile",
                    "TxDefaultProfile",
                    "TxProfile",
                ],
                "default_profile_purpose": "ChargePointMaxProfile",
                "supported_profile_kinds": ["Absolute"],
                "default_profile_kind": "Absolute",
                "supports_transaction_profile": True,
                "connector_count": connector_count,
            }

        if protocol == "ocpp2.1":
            return {
                "supports_remote_start": True,
                "supports_remote_stop": True,
                "supports_set_charging_profile": True,
                "supports_clear_charging_profile": True,
                "supports_bidirectional_power_transfer": True,
                "allowed_units": ["W", "A"],
                "preferred_unit": "W",
                "min_profile_limit": -500000.0,
                "max_profile_limit": 500000.0,
                "supported_profile_purposes": [
                    "ChargingStationMaxProfile",
                    "TxDefaultProfile",
                    "TxProfile",
                    "ChargingStationExternalConstraints",
                    "PriorityCharging",
                    "LocalGeneration",
                ],
                "default_profile_purpose": "ChargingStationMaxProfile",
                "supported_profile_kinds": ["Absolute", "Relative", "Dynamic"],
                "default_profile_kind": "Dynamic",
                "supports_transaction_profile": True,
                "supports_dynamic_profiles": True,
                "supports_profile_setpoint": True,
                "supports_discharge_limit": True,
                "supported_operation_modes": [
                    "ChargingOnly",
                    "ChargingAndDischarging",
                    "DischargingOnly",
                ],
                "default_operation_mode": "ChargingOnly",
                "connector_count": connector_count,
            }

        return {
            "supports_remote_start": True,
            "supports_remote_stop": True,
            "supports_set_charging_profile": True,
            "supports_clear_charging_profile": True,
            "supports_bidirectional_power_transfer": True,
            "allowed_units": ["W", "A"],
            "preferred_unit": "W",
            "min_profile_limit": -500000.0,
            "max_profile_limit": 500000.0,
            "supported_profile_purposes": [
                "ChargingStationMaxProfile",
                "TxDefaultProfile",
                "TxProfile",
            ],
            "default_profile_purpose": "ChargingStationMaxProfile",
            "supported_profile_kinds": ["Absolute", "Relative"],
            "default_profile_kind": "Absolute",
            "supports_transaction_profile": True,
            "supports_dynamic_profiles": False,
            "supports_profile_setpoint": False,
            "supports_discharge_limit": False,
            "supported_operation_modes": ["ChargingOnly"],
            "default_operation_mode": "ChargingOnly",
            "connector_count": connector_count,
        }
