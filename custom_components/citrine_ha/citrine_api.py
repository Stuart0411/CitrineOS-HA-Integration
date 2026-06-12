"""Async client for CitrineOS APIs."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
import json
import logging
import random
import re
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
        response = await self._request_with_fallback_paths(
            "POST",
            paths,
            params=params,
            json=payload,
        )
        return self._validate_command_response(
            action="request_start_transaction",
            station_id=station_id,
            response=response,
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
            response = await self._request_with_fallback_paths(
                "POST",
                paths,
                params=params,
                json=payload,
            )
            return self._validate_command_response(
                action="request_stop_transaction",
                station_id=station_id,
                response=response,
            )
        except CitrineApiError as err:
            # Some OCPP 2.x backends only accept integer-like transaction identifiers.
            if protocol != "ocpp1.6":
                try:
                    int_tx_id = int(transaction_id)
                except (TypeError, ValueError):
                    raise
                response = await self._request_with_fallback_paths(
                    "POST",
                    paths,
                    params=params,
                    json={"transactionId": int_tx_id},
                )
                return self._validate_command_response(
                    action="request_stop_transaction",
                    station_id=station_id,
                    response=response,
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
            schedule: dict[str, Any] = {
                "chargingRateUnit": normalized_unit,
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": round(limit, 1)}
                ],
            }
            if duration > 0:
                schedule["duration"] = duration

            payload = {
                "connectorId": evse_id,
                "csChargingProfiles": {
                    "chargingProfileId": random.randint(1000, 9999),
                    "stackLevel": 1,
                    "chargingProfilePurpose": "ChargePointMaxProfile",
                    "chargingProfileKind": "Absolute",
                    "chargingSchedule": schedule,
                },
            }
            paths = ["/ocpp/1.6/smartcharging/setChargingProfile"]
        else:
            start_schedule = self._iso_utc_now()
            schedule = {
                "id": random.randint(1000, 9999),
                "chargingRateUnit": normalized_unit,
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": round(limit, 1)}
                ],
            }
            if duration > 0:
                schedule["duration"] = duration

            payload = {
                "evseId": evse_id,
                "chargingProfile": {
                    "id": random.randint(1000, 9999),
                    "stackLevel": 1,
                    "chargingProfilePurpose": "ChargingStationMaxProfile",
                    "chargingProfileKind": "Absolute",
                    "chargingSchedule": [
                        {
                            **schedule,
                            "startSchedule": start_schedule,
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
            response = await self._request_with_fallback_paths(
                "POST",
                paths,
                params=params,
                json=payload,
            )
            return self._validate_command_response(
                action="set_station_limit",
                station_id=station_id,
                response=response,
            )
        except CitrineApiError as err:
            message = str(err).lower()
            # Common OCPP 1.6 quirk: connectorId 0 rejected, retry with connector 1.
            if protocol == "ocpp1.6" and evse_id == 0:
                retry_payload = dict(payload)
                retry_payload["connectorId"] = 1
                response = await self._request_with_fallback_paths(
                    "POST",
                    paths,
                    params=params,
                    json=retry_payload,
                )
                return self._validate_command_response(
                    action="set_station_limit",
                    station_id=station_id,
                    response=response,
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
                response = await self._request_with_fallback_paths(
                    "POST",
                    paths,
                    params=params,
                    json=retry_payload,
                )
                return self._validate_command_response(
                    action="set_station_limit",
                    station_id=station_id,
                    response=response,
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
        txprofile_compatibility_fallback: bool = True,
    ) -> Any:
        """Apply an explicit charging profile with OCPP-specific structure."""
        protocol = self.normalize_protocol(protocol)
        normalized_unit = unit.upper()
        normalized_purpose = str(profile_purpose or "TxProfile").strip()
        purpose_key = normalized_purpose.lower()

        # Some OCPP 2.x firmware crashes/drops websocket when TxProfile carries UUID transaction ids.
        # Downgrade to TxDefaultProfile in that case to keep EVSE online while still applying a limit.
        if (
            txprofile_compatibility_fallback
            and
            protocol != "ocpp1.6"
            and purpose_key == "txprofile"
            and transaction_id is not None
            and re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
                str(transaction_id),
            )
        ):
            _LOGGER.warning(
                "TxProfile compatibility fallback: station=%s protocol=%s tx_id=%s downgraded_to=TxDefaultProfile",
                station_id,
                protocol,
                transaction_id,
            )
            normalized_purpose = "TxDefaultProfile"
            purpose_key = "txdefaultprofile"
            transaction_id = None

        # Station/charge-point max profiles should be station scoped.
        if purpose_key in {"chargingstationmaxprofile", "chargepointmaxprofile"}:
            evse_id = 0

        payload_variants: list[dict[str, Any]] = []

        if protocol == "ocpp1.6":
            schedule: dict[str, Any] = {
                "chargingRateUnit": normalized_unit,
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": round(limit, 1)}
                ],
            }
            if duration > 0:
                schedule["duration"] = duration

            payload_base: dict[str, Any] = {
                "connectorId": evse_id,
                "csChargingProfiles": {
                    "chargingProfileId": profile_id or random.randint(1000, 9999),
                    "stackLevel": stack_level,
                    "chargingProfilePurpose": normalized_purpose,
                    "chargingProfileKind": "Absolute",
                    "chargingSchedule": schedule,
                },
            }
            payload_variants.append(payload_base)

            if transaction_id is not None and purpose_key == "txprofile":
                try:
                    tx_value: int | str = int(transaction_id)
                except (TypeError, ValueError):
                    tx_value = transaction_id

                # Variant 1: transactionId inside profile object.
                payload_profile_tx = deepcopy(payload_base)
                payload_profile_tx["csChargingProfiles"]["transactionId"] = tx_value
                payload_variants.insert(0, payload_profile_tx)

                # Variant 2: transactionId at request root.
                payload_root_tx = deepcopy(payload_base)
                payload_root_tx["transactionId"] = tx_value
                payload_variants.append(payload_root_tx)

                # Variant 3: include both locations for strict/legacy backends.
                payload_both_tx = deepcopy(payload_profile_tx)
                payload_both_tx["transactionId"] = tx_value
                payload_variants.append(payload_both_tx)

            paths = ["/ocpp/1.6/smartcharging/setChargingProfile"]
        else:
            start_schedule = self._iso_utc_now()
            txprofile_relative = purpose_key == "txprofile"
            default_kind = "Relative" if txprofile_relative else "Absolute"
            schedule: dict[str, Any] = {
                "id": random.randint(1000, 9999),
                "chargingRateUnit": normalized_unit,
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": round(limit, 1)}
                ],
            }
            if duration > 0:
                schedule["duration"] = duration

            payload_base = {
                "evseId": evse_id,
                "chargingProfile": {
                    "id": profile_id or random.randint(1000, 9999),
                    "stackLevel": stack_level,
                    "chargingProfilePurpose": normalized_purpose,
                    "chargingProfileKind": default_kind,
                    "chargingSchedule": [schedule],
                },
            }
            # startSchedule is mandatory for Absolute/Recurring, but not for Relative.
            if default_kind in {"Absolute", "Recurring"}:
                payload_base["chargingProfile"]["chargingSchedule"][0]["startSchedule"] = start_schedule
            payload_variants.append(payload_base)

            if transaction_id is not None and purpose_key == "txprofile":
                tx_value = str(transaction_id)

                # Variant 1: transactionId inside chargingProfile object (OCPP 2.x common).
                payload_profile_tx = deepcopy(payload_base)
                payload_profile_tx["chargingProfile"]["transactionId"] = tx_value
                payload_variants.insert(0, payload_profile_tx)

                # Variant 2: transactionId at request root (backend-specific).
                payload_root_tx = deepcopy(payload_base)
                payload_root_tx["transactionId"] = tx_value
                payload_variants.append(payload_root_tx)

                # Variant 3: include both locations for strict/legacy backends.
                payload_both_tx = deepcopy(payload_profile_tx)
                payload_both_tx["transactionId"] = tx_value
                payload_variants.append(payload_both_tx)

                # Compatibility fallback: some chargers reject Absolute TxProfile but accept Relative.
                payload_absolute_profile_tx = deepcopy(payload_profile_tx)
                payload_absolute_profile_tx["chargingProfile"]["chargingProfileKind"] = "Absolute"
                payload_absolute_profile_tx["chargingProfile"]["chargingSchedule"][0]["startSchedule"] = start_schedule
                payload_variants.append(payload_absolute_profile_tx)

                payload_absolute_root_tx = deepcopy(payload_root_tx)
                payload_absolute_root_tx["chargingProfile"]["chargingProfileKind"] = "Absolute"
                payload_absolute_root_tx["chargingProfile"]["chargingSchedule"][0]["startSchedule"] = start_schedule
                payload_variants.append(payload_absolute_root_tx)

                payload_absolute_both_tx = deepcopy(payload_both_tx)
                payload_absolute_both_tx["chargingProfile"]["chargingProfileKind"] = "Absolute"
                payload_absolute_both_tx["chargingProfile"]["chargingSchedule"][0]["startSchedule"] = start_schedule
                payload_variants.append(payload_absolute_both_tx)

            paths = [
                "/ocpp/2.0.1/smartcharging/setChargingProfile",
                "/ocpp/2.0/smartcharging/setChargingProfile",
            ]

        params = self._identifier_params(station_id)
        last_error: CitrineApiError | None = None
        for index, payload in enumerate(payload_variants, start=1):
            _LOGGER.warning(
                "set_charging_profile request variant=%s station=%s protocol=%s purpose=%s evse=%s payload=%s",
                index,
                station_id,
                protocol,
                normalized_purpose,
                evse_id,
                payload,
            )
            try:
                response = await self._request_with_fallback_paths(
                    "POST",
                    paths,
                    params=params,
                    json=payload,
                )
                _LOGGER.warning(
                    "set_charging_profile response variant=%s station=%s protocol=%s purpose=%s evse=%s response=%s",
                    index,
                    station_id,
                    protocol,
                    normalized_purpose,
                    evse_id,
                    response,
                )
                return self._validate_command_response(
                    action="set_charging_profile",
                    station_id=station_id,
                    response=response,
                )
            except CitrineApiError as err:
                last_error = err
                _LOGGER.warning(
                    "set_charging_profile variant failed: variant=%s station=%s error=%s",
                    index,
                    station_id,
                    err,
                )
                continue

        if last_error is not None:
            raise last_error
        raise CitrineApiError("set_charging_profile failed with no payload variants")

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
        response = await self._request_with_fallback_paths(
            "POST",
            paths,
            params=params,
            json=payload,
        )
        return self._validate_command_response(
            action="clear_charging_profile",
            station_id=station_id,
            response=response,
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

    def _validate_command_response(
        self,
        *,
        action: str,
        station_id: str,
        response: Any,
    ) -> Any:
        """Raise explicit errors when command payload indicates rejection despite HTTP success."""
        if isinstance(response, list):
            if not response:
                return response

            list_success_values: list[bool] = []
            for item in response:
                if isinstance(item, dict) and "success" in item and isinstance(item["success"], bool):
                    list_success_values.append(item["success"])

            if list_success_values and not all(list_success_values):
                compact = json.dumps(response, ensure_ascii=True, separators=(",", ":"))
                _LOGGER.warning(
                    "Citrine command rejected by success=false list payload: action=%s station=%s response=%s",
                    action,
                    station_id,
                    compact,
                )
                raise CitrineApiError(
                    f"Command {action} was not accepted for station {station_id}: {compact}"
                )
            return response

        if not isinstance(response, dict):
            return response

        tokens = self._extract_status_tokens(response)
        reject_markers = {
            "rejected",
            "reject",
            "notsupported",
            "not_supported",
            "unsupported",
            "denied",
            "failed",
            "error",
            "invalid",
            "faulted",
        }
        accept_markers = {
            "accepted",
            "accept",
            "ok",
            "success",
            "succeeded",
            "scheduled",
            "inprogress",
            "pending",
        }

        if "accepted" in response and isinstance(response["accepted"], bool):
            if not response["accepted"]:
                compact = json.dumps(response, ensure_ascii=True, separators=(",", ":"))
                _LOGGER.warning(
                    "Citrine command rejected by payload: action=%s station=%s response=%s",
                    action,
                    station_id,
                    compact,
                )
                raise CitrineApiError(
                    f"Command {action} was not accepted for station {station_id}: {compact}"
                )
            return response

        rejected = any(token in reject_markers for token in tokens)
        accepted = any(token in accept_markers for token in tokens)
        if rejected and not accepted:
            compact = json.dumps(response, ensure_ascii=True, separators=(",", ":"))
            _LOGGER.warning(
                "Citrine command rejected by status token: action=%s station=%s response=%s",
                action,
                station_id,
                compact,
            )
            raise CitrineApiError(
                f"Command {action} was rejected for station {station_id}: {compact}"
            )

        _LOGGER.debug(
            "Citrine command response: action=%s station=%s tokens=%s",
            action,
            station_id,
            sorted(tokens),
        )
        return response

    def _extract_status_tokens(self, payload: Any) -> set[str]:
        """Recursively collect lower-cased status-ish values from a response payload."""
        tokens: set[str] = set()

        def _walk(node: Any, depth: int) -> None:
            if depth > 6:
                return
            if isinstance(node, dict):
                for key, value in node.items():
                    key_l = str(key).lower()
                    if isinstance(value, str) and (
                        "status" in key_l
                        or "result" in key_l
                        or key_l in {"message", "reason", "state", "outcome"}
                    ):
                        tokens.add(value.strip().lower().replace(" ", ""))
                    _walk(value, depth + 1)
                return
            if isinstance(node, list):
                for item in node:
                    _walk(item, depth + 1)

        _walk(payload, 0)
        return tokens

    @staticmethod
    def _iso_utc_now() -> str:
        """Return current UTC timestamp in RFC3339 format expected by OCPP 2.x."""
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
