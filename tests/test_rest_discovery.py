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


@pytest.mark.asyncio
async def test_get_charging_stations_prefers_ocpprouter_endpoint_first():
    """Station discovery should prioritize OCPP router endpoint for live command identifiers."""
    session_mock = MagicMock()
    client = CitrineClient(
        session=session_mock,
        base_url="http://localhost:8080",
        tenant_id=1,
        auth_token="secret",
        verify_ssl=False,
        request_timeout=10,
    )

    client._request_with_fallback_paths = AsyncMock(return_value=[{"identifier": "10919352"}])
    stations = await client.get_charging_stations()

    assert stations == [{"identifier": "10919352"}]
    called_paths = client._request_with_fallback_paths.await_args.kwargs["paths"]
    assert called_paths[0] == "/data/ocpprouter/chargingStation"


@pytest.mark.asyncio
async def test_set_station_limit_retries_with_discovered_identifier():
    """When DB id has no active connection, retry with discovered runtime identifier."""
    session_mock = MagicMock()
    client = CitrineClient(
        session=session_mock,
        base_url="http://localhost:8080",
        tenant_id=1,
        auth_token="secret",
        verify_ssl=False,
        request_timeout=10,
    )

    client.get_charging_stations = AsyncMock(
        return_value=[
            {
                "id": 2,
                "identifier": "10919352",
                "protocol": "ocpp2.1",
            }
        ]
    )
    client._request_with_fallback_paths = AsyncMock(
        side_effect=[
            [{"success": False, "payload": "No connection found for identifier: 1:2"}],
            [{"success": True, "payload": {"status": "Accepted"}}],
        ]
    )

    response = await client.set_station_limit(
        protocol="ocpp2.1",
        station_id="2",
        limit=7400.0,
        unit="W",
        evse_id=0,
        duration=300,
    )

    assert isinstance(response, list)
    assert response[0]["success"] is True
    assert client._request_with_fallback_paths.await_count == 2
    first_params = client._request_with_fallback_paths.await_args_list[0].kwargs["params"]
    second_params = client._request_with_fallback_paths.await_args_list[1].kwargs["params"]
    assert first_params["identifier"] == "2"
    assert second_params["identifier"] == "10919352"


@pytest.mark.asyncio
async def test_set_station_limit_retries_with_connector_identifier_fallback():
    """Fallback can resolve runtime identifier from connector rows when station row has only DB id."""
    session_mock = MagicMock()
    client = CitrineClient(
        session=session_mock,
        base_url="http://localhost:8080",
        tenant_id=1,
        auth_token="secret",
        verify_ssl=False,
        request_timeout=10,
    )

    client.get_charging_stations = AsyncMock(return_value=[{"id": 2, "protocol": "ocpp2.1"}])
    client.get_connectors = AsyncMock(
        return_value=[
            {
                "stationId": 2,
                "chargingStationId": "10919352",
                "connectorId": 1,
                "evseId": 1,
            }
        ]
    )
    client.get_transactions = AsyncMock(return_value=[])
    client._request_with_fallback_paths = AsyncMock(
        side_effect=[
            [{"success": False, "payload": "No connection found for identifier: 1:2"}],
            [{"success": True, "payload": {"status": "Accepted"}}],
        ]
    )

    response = await client.set_station_limit(
        protocol="ocpp2.1",
        station_id="2",
        limit=6058.0,
        unit="W",
        evse_id=0,
        duration=300,
    )

    assert isinstance(response, list)
    assert response[0]["success"] is True
    assert client._request_with_fallback_paths.await_count == 2
    first_params = client._request_with_fallback_paths.await_args_list[0].kwargs["params"]
    second_params = client._request_with_fallback_paths.await_args_list[1].kwargs["params"]
    assert first_params["identifier"] == "2"
    assert second_params["identifier"] == "10919352"


@pytest.mark.asyncio
async def test_set_station_limit_retries_with_global_candidate_pool():
    """When station-key matching fails, fallback should still try small global identifier pool."""
    session_mock = MagicMock()
    client = CitrineClient(
        session=session_mock,
        base_url="http://localhost:8080",
        tenant_id=1,
        auth_token="secret",
        verify_ssl=False,
        request_timeout=10,
    )

    # No direct match to requested key, but discovery contains one plausible runtime identifier.
    client.get_charging_stations = AsyncMock(
        return_value=[{"id": 2, "chargePointSerialNumber": "SN2507190633"}]
    )
    client.get_connectors = AsyncMock(
        return_value=[{"stationId": "10919352", "connectorId": 1, "evseId": 1}]
    )
    client.get_transactions = AsyncMock(return_value=[])
    client._request_with_fallback_paths = AsyncMock(
        side_effect=[
            [{"success": False, "payload": "No connection found for identifier: 1:SN2507190633"}],
            [{"success": True, "payload": {"status": "Accepted"}}],
        ]
    )

    response = await client.set_station_limit(
        protocol="ocpp2.1",
        station_id="SN2507190633",
        limit=7400.0,
        unit="W",
        evse_id=0,
        duration=300,
    )

    assert isinstance(response, list)
    assert response[0]["success"] is True
    assert client._request_with_fallback_paths.await_count == 2
    first_params = client._request_with_fallback_paths.await_args_list[0].kwargs["params"]
    second_params = client._request_with_fallback_paths.await_args_list[1].kwargs["params"]
    assert first_params["identifier"] == "SN2507190633"
    assert second_params["identifier"] == "10919352"
