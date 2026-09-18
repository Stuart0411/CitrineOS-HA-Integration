"""MQTT Intent Publisher for CitrineOS EMS integration."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class MqttIntentPublisher:
    """Publishes structured site load-intent envelopes to CitrineOS MQTT broker."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        site_id: str = "home-site-1",
        topic_prefix: str = "citrine/ems",
        ttl_seconds: int = 60,
    ) -> None:
        self.hass = hass
        self.site_id = site_id
        self.topic_prefix = topic_prefix.strip("/")
        self.ttl_seconds = max(10, min(int(ttl_seconds), 3600))

        self.last_published_at: str | None = None
        self.publish_count: int = 0
        self.publish_failure_count: int = 0
        self.last_attempt_count: int = 0
        self.last_publish_success: bool = True
        self.last_error: str | None = None

    @property
    def intent_topic(self) -> str:
        """Construct the canonical MQTT intent topic for this site."""
        return f"{self.topic_prefix}/site/{self.site_id}/intent/current"

    def is_mqtt_available(self) -> bool:
        """Check if Home Assistant MQTT integration is loaded and active."""
        return self.hass.services.has_service("mqtt", "publish")

    async def async_publish_intent(
        self,
        *,
        operation_mode: str,
        reason: str,
        max_import_power_w: float,
        max_export_power_w: float,
        allocated_ev_power_w: float,
        solar_surplus_w: float,
        site_headroom_w: float,
        allocations: list[dict[str, Any]],
        fallback_limit_w: float = 1380.0,
    ) -> bool:
        """
        Build and publish a dynamic intent envelope.
        Returns True if published successfully via MQTT, False otherwise.
        """
        if not self.is_mqtt_available():
            self.last_publish_success = False
            self.last_error = "MQTT integration not available in Home Assistant"
            return False

        now = datetime.now(UTC)
        now_iso = self._format_utc(now)
        expires_iso = self._format_utc(now + timedelta(seconds=self.ttl_seconds))
        ems_mode = self._normalize_operation_mode(operation_mode)
        allow_discharge = any(
            float(allocation.get("dischargeLimitW", 0) or 0) > 0
            for allocation in allocations
        )
        payload = {
            # Native CitrineOS EMS intent contract.
            "messageId": str(uuid4()),
            "siteId": self.site_id,
            "source": {
                "system": "home-assistant",
                "component": "citrine_ha",
                "instance": self.site_id,
            },
            "createdAt": now_iso,
            "expiresAt": expires_iso,
            "mode": ems_mode,
            "constraints": {
                "maxImportW": round(float(max_import_power_w), 1),
                "maxExportW": round(float(max_export_power_w), 1),
                "evChargeBudgetW": round(float(allocated_ev_power_w), 1),
                "evDischargeBudgetW": round(
                    sum(float(item.get("dischargeLimitW", 0) or 0) for item in allocations),
                    1,
                ),
            },
            "flags": {
                "allowDischarge": allow_discharge,
                "emergencyCurtailment": ems_mode == "Idle",
            },
            "reason": reason,
            # Legacy/diagnostic fields retained for existing consumers.
            "$schema": "https://citrineos.github.io/schemas/ems/intent/v1.json",
            "schemaVersion": "1.1.0",
            "timestamp": now_iso,
            "ttlSeconds": self.ttl_seconds,
            "operationMode": operation_mode,
            "siteLimits": {
                "maxImportPowerW": round(float(max_import_power_w), 1),
                "maxExportPowerW": round(float(max_export_power_w), 1),
                "allocatedEvPowerW": round(float(allocated_ev_power_w), 1),
                "solarSurplusPowerW": round(float(solar_surplus_w), 1),
                "siteHeadroomW": round(float(site_headroom_w), 1),
            },
            "allocations": allocations,
            "safetyFallback": {
                "onTtlExpiryAction": "SafeClamp",
                "fallbackLimitW": round(float(fallback_limit_w), 1),
            },
            "metadata": {
                "source": "home-assistant-citrine_ha",
                "version": "0.2.0",
                "nativeContract": "EmsSiteIntentCreate",
            },
        }

        payload_str = json.dumps(payload)
        publish_data = {
            "topic": self.intent_topic,
            "payload": payload_str,
            "retain": False,
            "qos": 0,
        }

        self.last_attempt_count = 0
        last_error: Exception | None = None
        for attempt in range(1, 4):
            self.last_attempt_count = attempt
            try:
                await self.hass.services.async_call(
                    "mqtt",
                    "publish",
                    publish_data,
                    blocking=True,
                )
                self.last_published_at = now_iso
                self.publish_count += 1
                self.last_publish_success = True
                self.last_error = None
                _LOGGER.debug(
                    "Published EMS site intent to %s (attempt=%s allocated=%.1fW stations=%d)",
                    self.intent_topic,
                    attempt,
                    allocated_ev_power_w,
                    len(allocations),
                )
                return True
            except Exception as err:  # noqa: BLE001
                last_error = err
                _LOGGER.warning(
                    "MQTT intent publish failed (attempt=%s/3) to %s: %s",
                    attempt,
                    self.intent_topic,
                    err,
                )

        self.publish_failure_count += 1
        self.last_publish_success = False
        self.last_error = str(last_error) if last_error else "Unknown MQTT publish failure"
        return False

    @staticmethod
    def _normalize_operation_mode(operation_mode: str) -> str:
        """Map HA-friendly mode labels to valid CitrineOS EMS modes."""
        if operation_mode in {"Off", "Emergency Safe"}:
            return "Idle"
        return "ExternalLimits"

    @staticmethod
    def _format_utc(value: datetime) -> str:
        """Format UTC timestamps using the strict CitrineOS ISO-8601 contract."""
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
