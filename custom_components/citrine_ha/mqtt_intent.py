"""MQTT Intent Publisher for CitrineOS EMS integration."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

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

        now_iso = datetime.now(UTC).isoformat()
        payload = {
            "$schema": "https://citrineos.github.io/schemas/ems/intent/v1.json",
            "schemaVersion": "1.1.0",
            "siteId": self.site_id,
            "timestamp": now_iso,
            "ttlSeconds": self.ttl_seconds,
            "operationMode": operation_mode,
            "reason": reason,
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
            },
        }

        try:
            payload_str = json.dumps(payload)
            await self.hass.services.async_call(
                "mqtt",
                "publish",
                {
                    "topic": self.intent_topic,
                    "payload": payload_str,
                    "retain": False,
                    "qos": 0,
                },
                blocking=True,
            )
            self.last_published_at = now_iso
            self.publish_count += 1
            self.last_publish_success = True
            self.last_error = None
            _LOGGER.debug(
                "Published EMS site intent to %s (Allocated %.1fW across %d stations)",
                self.intent_topic,
                allocated_ev_power_w,
                len(allocations),
            )
            return True
        except Exception as err:  # noqa: BLE001
            self.last_publish_success = False
            self.last_error = str(err)
            _LOGGER.warning("Failed to publish MQTT intent to %s: %s", self.intent_topic, err)
            return False
