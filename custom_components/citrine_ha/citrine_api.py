"""Async client for CitrineOS APIs."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Mapping
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class CitrineApiError(Exception):
    """Base API error."""


class CitrineAuthError(CitrineApiError):
    """Authentication failed."""


class CitrineClient:
    """CitrineOS API client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        tenant_id: int,
        auth_token: str | None,
        verify_ssl: bool,
        request_timeout: int,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._tenant_id = tenant_id
        self._auth_token = auth_token
        self._verify_ssl = verify_ssl
        self._request_timeout = request_timeout

    async def ping(self) -> None:
        """Validate API reachability and credentials."""
        await self._request("GET", "/data/ocpprouter/systemConfig")

    async def request_start_transaction(
        self,
        *,
        protocol: str,
        station_id: str,
        id_tag: str,
        evse_id: int | None = None,
        remote_start_id: int | None = None,
    ) -> Any:
        """Start remote charging for 1.6 or 2.x stations."""
        if protocol == "ocpp1.6":
            payload: dict[str, Any] = {"idTag": id_tag}
            if evse_id is not None:
                payload["connectorId"] = evse_id
            path = "/ocpp/1.6/evdriver/remoteStartTransaction"
        else:
            payload = {
                "remoteStartId": remote_start_id or random.randint(100000, 999999),
                "idToken": {"idToken": id_tag, "type": "Local"},
            }
            if evse_id is not None:
                payload["evseId"] = evse_id
            path = "/ocpp/2.0.1/evdriver/requestStartTransaction"

        params = self._identifier_params(station_id)
        return await self._request("POST", path, params=params, json=payload)

    async def request_stop_transaction(
        self,
        *,
        protocol: str,
        station_id: str,
        transaction_id: str,
    ) -> Any:
        """Stop remote charging for 1.6 or 2.x stations."""
        if protocol == "ocpp1.6":
            try:
                v16_txn: int | str = int(transaction_id)
            except ValueError:
                v16_txn = transaction_id
            payload = {"transactionId": v16_txn}
            path = "/ocpp/1.6/evdriver/remoteStopTransaction"
        else:
            payload = {"transactionId": str(transaction_id)}
            path = "/ocpp/2.0.1/evdriver/requestStopTransaction"

        params = self._identifier_params(station_id)
        return await self._request("POST", path, params=params, json=payload)

    async def set_station_limit(
        self,
        *,
        protocol: str,
        station_id: str,
        limit: float,
        unit: str = "W",
        evse_id: int = 0,
        duration: int = 300,
    ) -> Any:
        """Apply a station-level smart charging limit."""
        normalized_unit = unit.upper()

        if protocol == "ocpp1.6":
            payload = {
                "connectorId": evse_id,
                "csChargingProfiles": {
                    "chargingProfileId": random.randint(1000, 9999),
                    "stackLevel": 1,
                    "chargingProfilePurpose": "ChargePointMaxProfile",
                    "chargingProfileKind": "Absolute",
                    "chargingSchedule": {
                        "duration": duration,
                        "chargingRateUnit": normalized_unit,
                        "chargingSchedulePeriod": [
                            {"startPeriod": 0, "limit": round(limit, 1)}
                        ],
                    },
                },
            }
            path = "/ocpp/1.6/smartcharging/setChargingProfile"
        else:
            payload = {
                "evseId": evse_id,
                "chargingProfile": {
                    "id": random.randint(1000, 9999),
                    "stackLevel": 1,
                    "chargingProfilePurpose": "ChargingStationMaxProfile",
                    "chargingProfileKind": "Absolute",
                    "chargingSchedule": [
                        {
                            "id": random.randint(1000, 9999),
                            "duration": duration,
                            "chargingRateUnit": normalized_unit,
                            "chargingSchedulePeriod": [
                                {"startPeriod": 0, "limit": round(limit, 1)}
                            ],
                        }
                    ],
                },
            }
            path = "/ocpp/2.0.1/smartcharging/setChargingProfile"

        params = self._identifier_params(station_id)
        return await self._request("POST", path, params=params, json=payload)

    async def set_group_limit(
        self,
        *,
        group_id: str,
        station_protocols: Mapping[str, str],
        total_limit: float,
        unit: str,
        duration: int,
    ) -> dict[str, Any]:
        """Split a group cap equally and dispatch station limit commands."""
        if not station_protocols:
            return {"group_id": group_id, "results": {}}

        per_station = total_limit / len(station_protocols)
        results: dict[str, Any] = {}
        for station_id, protocol in station_protocols.items():
            try:
                results[station_id] = await self.set_station_limit(
                    protocol=protocol,
                    station_id=station_id,
                    limit=per_station,
                    unit=unit,
                    duration=duration,
                )
            except CitrineApiError as err:
                results[station_id] = {"error": str(err)}

        return {"group_id": group_id, "results": results}

    def _identifier_params(self, station_id: str) -> dict[str, Any]:
        return {
            "identifier": station_id,
            "tenantId": self._tenant_id,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        url = f"{self._base_url}{path}"
        timeout = aiohttp.ClientTimeout(total=self._request_timeout)

        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=json,
                headers=headers,
                ssl=self._verify_ssl,
                timeout=timeout,
            ) as resp:
                if resp.status in (401, 403):
                    raise CitrineAuthError("Authentication or authorization failed")

                text = await resp.text()
                if resp.status >= 400:
                    raise CitrineApiError(
                        f"Citrine API {method} {path} failed ({resp.status}): {text}"
                    )

                if not text:
                    return None

                content_type = resp.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    return await resp.json()

                return text
        except asyncio.TimeoutError as err:
            raise CitrineApiError(f"Citrine API timeout calling {path}") from err
        except aiohttp.ClientError as err:
            raise CitrineApiError(f"Citrine API transport error: {err}") from err
