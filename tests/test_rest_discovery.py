"""Tests for direct CitrineOS REST discovery (Zero-Hasura)."""

from unittest.mock import AsyncMock, MagicMock
import pytest
from custom_components.citrine_ha.citrine_api import CitrineClient


@pytest.mark.asyncio
async def test_extract_items_from_response():
    """Verify robust extraction of station arrays across varied JSON formats."""
    # List directly
    assert CitrineClient._extract_items_from_response([{"id": "CS1"}]) == [{"id": "CS1"}]

    # Wrapped under 'data'
    assert CitrineClient._extract_items_from_response({"data": [{"id": "CS2"}]}) == [{"id": "CS2"}]

    # Wrapped under 'rows'
    assert CitrineClient._extract_items_from_response({"rows": [{"id": "CS3"}]}) == [{"id": "CS3"}]

    # Single object dict
    assert CitrineClient._extract_items_from_response({"id": "CS4", "protocol": "ocpp2.0.1"}) == [
        {"id": "CS4", "protocol": "ocpp2.0.1"}
    ]

    # Non-matching dict
    assert CitrineClient._extract_items_from_response({"status": "ok"}) == []


@pytest.mark.asyncio
async def test_discover_chargers_rest_pipeline():
    """Verify discover_chargers_rest combines stations, connectors, and transactions."""
    session_mock = MagicMock()
    client = CitrineClient(
        session=session_mock,
        base_url="http://localhost:8080",
        tenant_id=1,
        auth_token="secret",
        verify_ssl=False,
        request_timeout=10,
    )

    client.get_charging_stations = AsyncMock(return_value=[{"id": "CS100", "protocol": "ocpp2.0.1"}])
    client.get_connectors = AsyncMock(return_value=[{"stationId": "CS100", "connectorId": 1, "evseId": 1}])
    client.get_transactions = AsyncMock(return_value=[{"stationId": "CS100", "transactionId": "tx-999", "isActive": True}])

    result = await client.discover_chargers_rest()
    assert len(result["stations"]) == 1
    assert result["stations"][0]["id"] == "CS100"
    assert len(result["connectors"]) == 1
    assert len(result["transactions"]) == 1
