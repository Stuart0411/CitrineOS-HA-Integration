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

    @staticmethod
    def normalize_protocol(protocol: str | None) -> str:
        """Normalize protocol labels to supported API families."""
        if not protocol:
            return "ocpp2.0.1"

        value = str(protocol).strip().lower().replace(" ", "")
        if value in {"1.6", "ocpp1.6", "ocpp16", "ocpp-1.6"}:
            return "ocpp1.6"
        if value in {"2.0", "2.0.1", "ocpp2.0", "ocpp2.0.1", "ocpp201", "ocpp-2.0.1"}:
            return "ocpp2.0.1"
        return "ocpp2.0.1"

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
        protocol = self.normalize_protocol(protocol)

        if protocol == "ocpp1.6":
            payload: dict[str, Any] = {"idTag": id_tag}
            if evse_id is not None:
                payload["connectorId"] = evse_id
            paths = ["/ocpp/1.6/evdriver/remoteStartTransaction"]
        else:
            payload = {
                "remoteStartId": remote_start_id or random.randint(100000, 999999),
                "idToken": {"idToken": id_tag, "type": "Local"},
            }
            if evse_id is not None:
                payload["evseId"] = evse_id
            paths = [
                "/ocpp/2.0.1/evdriver/requestStartTransaction",
                "/ocpp/2.0/evdriver/requestStartTransaction",
            ]

        params = self._identifier_params(station_id)
        return await self._request_with_fallback_paths(
            "POST",
            paths,
            params=params,
            json=payload,
        )

    async def request_stop_transaction(
        self,
        *,
        protocol: str,
        station_id: str,
        transaction_id: str,
    ) -> Any:
        """Stop remote charging for 1.6 or 2.x stations."""
        protocol = self.normalize_protocol(protocol)

        if protocol == "ocpp1.6":
            try:
                v16_txn: int | str = int(transaction_id)
            except ValueError:
                v16_txn = transaction_id
            payload = {"transactionId": v16_txn}
            paths = ["/ocpp/1.6/evdriver/remoteStopTransaction"]
        else:
            payload = {"transactionId": str(transaction_id)}
            paths = [
                "/ocpp/2.0.1/evdriver/requestStopTransaction",
                "/ocpp/2.0/evdriver/requestStopTransaction",
            ]

        params = self._identifier_params(station_id)
        try:
            return await self._request_with_fallback_paths(
                "POST",
                paths,
                params=params,
                json=payload,
            )
        except CitrineApiError as err:
            # Some OCPP 2.x backends only accept integer-like transaction identifiers.
            if protocol != "ocpp1.6":
                try:
                    int_tx_id = int(transaction_id)
                except (TypeError, ValueError):
                    raise
                return await self._request_with_fallback_paths(
                    "POST",
                    paths,
                    params=params,
                    json={"transactionId": int_tx_id},
                )
            raise err

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
        protocol = self.normalize_protocol(protocol)
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
            paths = ["/ocpp/1.6/smartcharging/setChargingProfile"]
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
            paths = [
                "/ocpp/2.0.1/smartcharging/setChargingProfile",
                "/ocpp/2.0/smartcharging/setChargingProfile",
            ]

        params = self._identifier_params(station_id)
        try:
            return await self._request_with_fallback_paths(
                "POST",
                paths,
                params=params,
                json=payload,
            )
        except CitrineApiError as err:
            message = str(err).lower()
            # Common OCPP 1.6 quirk: connectorId 0 rejected, retry with connector 1.
            if protocol == "ocpp1.6" and evse_id == 0:
                retry_payload = dict(payload)
                retry_payload["connectorId"] = 1
                return await self._request_with_fallback_paths(
                    "POST",
                    paths,
                    params=params,
                    json=retry_payload,
                )

            # Some stations reject W and only accept A in profile schedules.
            if protocol != "ocpp1.6" and normalized_unit == "W" and "chargingrateunit" in message:
                retry_payload = {
                    **payload,
                    "chargingProfile": {
                        **payload["chargingProfile"],
                        "chargingSchedule": [
                            {
                                **payload["chargingProfile"]["chargingSchedule"][0],
                                "chargingRateUnit": "A",
                            }
                        ],
                    },
                }
                return await self._request_with_fallback_paths(
                    "POST",
                    paths,
                    params=params,
                    json=retry_payload,
                )
            raise err

    async def set_charging_profile(
        self,
        *,
        protocol: str,
        station_id: str,
        limit: float,
        unit: str = "W",
        evse_id: int = 0,
        duration: int = 300,
        stack_level: int = 1,
        profile_id: int | None = None,
        profile_purpose: str | None = None,
        transaction_id: str | None = None,
    ) -> Any:
        """Apply an explicit charging profile with OCPP-specific structure."""
        protocol = self.normalize_protocol(protocol)
        normalized_unit = unit.upper()

        if protocol == "ocpp1.6":
            payload: dict[str, Any] = {
                "connectorId": evse_id,
                "csChargingProfiles": {
                    "chargingProfileId": profile_id or random.randint(1000, 9999),
                    "stackLevel": stack_level,
                    "chargingProfilePurpose": profile_purpose or "TxProfile",
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
            if transaction_id is not None:
                try:
                    payload["transactionId"] = int(transaction_id)
                except (TypeError, ValueError):
                    payload["transactionId"] = transaction_id

            paths = ["/ocpp/1.6/smartcharging/setChargingProfile"]
        else:
            payload = {
                "evseId": evse_id,
                "chargingProfile": {
                    "id": profile_id or random.randint(1000, 9999),
                    "stackLevel": stack_level,
                    "chargingProfilePurpose": profile_purpose or "TxProfile",
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
            if transaction_id is not None:
                payload["transactionId"] = str(transaction_id)

            paths = [
                "/ocpp/2.0.1/smartcharging/setChargingProfile",
                "/ocpp/2.0/smartcharging/setChargingProfile",
            ]

        params = self._identifier_params(station_id)
        return await self._request_with_fallback_paths(
            "POST",
            paths,
            params=params,
            json=payload,
        )

    async def clear_charging_profile(
        self,
        *,
        protocol: str,
        station_id: str,
        evse_id: int = 0,
        profile_id: int | None = None,
        stack_level: int | None = None,
        profile_purpose: str | None = None,
    ) -> Any:
        """Clear charging profile(s) for a station using protocol-specific payloads."""
        protocol = self.normalize_protocol(protocol)

        if protocol == "ocpp1.6":
            payload: dict[str, Any] = {}
            if profile_id is not None:
                payload["id"] = profile_id
            if stack_level is not None:
                payload["stackLevel"] = stack_level
            if profile_purpose:
                payload["chargingProfilePurpose"] = profile_purpose
            if evse_id:
                payload["connectorId"] = evse_id

            paths = ["/ocpp/1.6/smartcharging/clearChargingProfile"]
        else:
            criteria: dict[str, Any] = {}
            if profile_id is not None:
                criteria["id"] = profile_id
            if stack_level is not None:
                criteria["stackLevel"] = stack_level
            if profile_purpose:
                criteria["chargingProfilePurpose"] = profile_purpose

            payload = {"evseId": evse_id, "chargingProfileCriteria": criteria}
            paths = [
                "/ocpp/2.0.1/smartcharging/clearChargingProfile",
                "/ocpp/2.0/smartcharging/clearChargingProfile",
            ]

        params = self._identifier_params(station_id)
        return await self._request_with_fallback_paths(
            "POST",
            paths,
            params=params,
            json=payload,
        )

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

    async def _request_with_fallback_paths(
        self,
        method: str,
        paths: list[str],
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Try equivalent endpoints in sequence for backend compatibility."""
        last_error: CitrineApiError | None = None
        for path in paths:
            try:
                return await self._request(
                    method,
                    path,
                    params=params,
                    json=json,
                )
            except CitrineApiError as err:
                last_error = err
                message = str(err).lower()
                if "failed (404)" in message or "failed (405)" in message:
                    continue
                raise

        if last_error is not None:
            raise last_error
        raise CitrineApiError("No endpoint paths provided")
