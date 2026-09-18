"""Unit and scenario tests for the real-time MQTT intent publisher."""

from unittest.mock import AsyncMock, MagicMock
import json
import pytest
from custom_components.citrine_ha.mqtt_intent import MqttIntentPublisher


@pytest.mark.asyncio
async def test_mqtt_publish_payload_schema():
    """Verify published intent adheres to CSIP-Aus / DOE envelope schema."""
    hass_mock = MagicMock()
    hass_mock.services.has_service.return_value = True
    hass_mock.services.async_call = AsyncMock()

    publisher = MqttIntentPublisher(
        hass_mock,
        site_id="site-test-101",
        topic_prefix="citrine/ems",
        ttl_seconds=60,
    )

    assert publisher.intent_topic == "citrine/ems/site/site-test-101/intent/current"
    assert publisher.is_mqtt_available() is True

    allocations = [
        {
            "stationId": "CS01",
            "evseId": 1,
            "protocol": "ocpp2.0.1",
            "chargeLimitW": 4140.0,
            "dischargeLimitW": 0.0,
            "priority": 3,
            "phases": 1,
            "overrideMode": "auto",
            "unit": "W",
        }
    ]

    success = await publisher.async_publish_intent(
        operation_mode="ExternalLimits",
        reason="Tracking Solar",
        max_import_power_w=14400.0,
        max_export_power_w=5000.0,
        allocated_ev_power_w=4140.0,
        solar_surplus_w=4500.0,
        site_headroom_w=10000.0,
        allocations=allocations,
        fallback_limit_w=1380.0,
    )

    assert success is True
    assert publisher.publish_count == 1
    assert publisher.last_publish_success is True
    assert publisher.last_error is None

    # Verify service call arguments
    hass_mock.services.async_call.assert_called_once()
    call_args = hass_mock.services.async_call.call_args[0]
    domain, service, data = call_args

    assert domain == "mqtt"
    assert service == "publish"
    assert data["topic"] == "citrine/ems/site/site-test-101/intent/current"

    payload = json.loads(data["payload"])
    assert payload["schemaVersion"] == "1.1.0"
    assert payload["siteId"] == "site-test-101"
    assert payload["operationMode"] == "ExternalLimits"
    assert payload["mode"] == "ExternalLimits"
    assert payload["messageId"]
    assert payload["source"]["system"] == "home-assistant"
    assert payload["createdAt"].endswith("Z")
    assert payload["expiresAt"].endswith("Z")
    assert payload["reason"] == "Tracking Solar"
    assert payload["ttlSeconds"] == 60
    assert payload["constraints"]["maxImportW"] == 14400.0
    assert payload["constraints"]["maxExportW"] == 5000.0
    assert payload["constraints"]["evChargeBudgetW"] == 4140.0
    assert payload["constraints"]["evDischargeBudgetW"] == 0.0
    assert payload["siteLimits"]["maxImportPowerW"] == 14400.0
    assert payload["siteLimits"]["allocatedEvPowerW"] == 4140.0
    assert payload["siteLimits"]["solarSurplusPowerW"] == 4500.0
    assert payload["safetyFallback"]["fallbackLimitW"] == 1380.0
    assert payload["allocations"][0]["stationId"] == "CS01"


@pytest.mark.asyncio
async def test_mqtt_unavailable_graceful_handling():
    """Verify publisher gracefully reports failure when MQTT service is missing."""
    hass_mock = MagicMock()
    # MQTT not installed in HA
    hass_mock.services.has_service.return_value = False

    publisher = MqttIntentPublisher(hass_mock, site_id="site-test")
    assert publisher.is_mqtt_available() is False

    success = await publisher.async_publish_intent(
        operation_mode="Off",
        reason="Off",
        max_import_power_w=10000.0,
        max_export_power_w=0.0,
        allocated_ev_power_w=0.0,
        solar_surplus_w=0.0,
        site_headroom_w=10000.0,
        allocations=[],
    )

    assert success is False
    assert publisher.last_publish_success is False
    assert "MQTT integration not available" in str(publisher.last_error)


@pytest.mark.asyncio
async def test_mqtt_publish_retries_transient_failure():
    """A transient MQTT service error is retried and eventually succeeds."""
    hass_mock = MagicMock()
    hass_mock.services.has_service.return_value = True
    hass_mock.services.async_call = AsyncMock(
        side_effect=[RuntimeError("temporary broker failure"), None]
    )

    publisher = MqttIntentPublisher(hass_mock, site_id="site-retry")
    success = await publisher.async_publish_intent(
        operation_mode="Solar Only",
        reason="Tracking Solar",
        max_import_power_w=30000.0,
        max_export_power_w=5000.0,
        allocated_ev_power_w=5750.0,
        solar_surplus_w=6000.0,
        site_headroom_w=30000.0,
        allocations=[],
    )

    assert success is True
    assert publisher.last_attempt_count == 2
    assert publisher.publish_count == 1
    assert publisher.publish_failure_count == 0
    assert hass_mock.services.async_call.await_count == 2


@pytest.mark.asyncio
async def test_mqtt_publish_reports_exhausted_retries():
    """Three failed publish attempts produce one observable delivery failure."""
    hass_mock = MagicMock()
    hass_mock.services.has_service.return_value = True
    hass_mock.services.async_call = AsyncMock(side_effect=RuntimeError("broker offline"))

    publisher = MqttIntentPublisher(hass_mock, site_id="site-failure")
    success = await publisher.async_publish_intent(
        operation_mode="Emergency Safe",
        reason="Safe fallback",
        max_import_power_w=30000.0,
        max_export_power_w=5000.0,
        allocated_ev_power_w=0.0,
        solar_surplus_w=0.0,
        site_headroom_w=30000.0,
        allocations=[],
    )

    assert success is False
    assert publisher.last_attempt_count == 3
    assert publisher.publish_count == 0
    assert publisher.publish_failure_count == 1
    assert publisher.last_publish_success is False
    assert publisher.last_error == "broker offline"
