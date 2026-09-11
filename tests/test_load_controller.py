"""Closed-loop behavioral tests for CitrineLoadController."""

from unittest.mock import AsyncMock, MagicMock
from datetime import UTC, datetime
import pytest
from custom_components.citrine_ha.const import (
    CONF_BATTERY_MIN_SOC,
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_CONTROLLER_MODE,
    CONF_DEADBAND_W,
    CONF_GRID_POWER_SENSOR,
    CONF_MAIN_FUSE_LIMIT_W,
    CONF_MIN_CHARGE_CURRENT_A,
    CONF_MIN_DWELL_SECS,
    CONF_NOMINAL_VOLTAGE,
    CONF_RAMP_RATE_W_S,
    CONF_SITE_EXPORT_LIMIT_W,
    CONF_SITE_PHASES,
    CONF_SOLAR_POWER_SENSOR,
    CONF_SOLAR_START_BUFFER_W,
    MODE_EMERGENCY_SAFE,
    MODE_GRID_CAPPED,
    MODE_OFF,
    MODE_SOLAR_BATTERY,
    MODE_SOLAR_ONLY,
)
from custom_components.citrine_ha.load_controller import CitrineLoadController


@pytest.fixture
def mock_controller_env():
    """Build mock HA environment with coordinator and client."""
    hass = MagicMock()
    entry = MagicMock()
    coordinator = MagicMock()
    client = MagicMock()

    entry.data = {
        CONF_CONTROLLER_MODE: MODE_SOLAR_ONLY,
        CONF_MAIN_FUSE_LIMIT_W: 14400.0,
        CONF_SITE_EXPORT_LIMIT_W: 5000.0,
        CONF_SOLAR_START_BUFFER_W: 250.0,
        CONF_MIN_CHARGE_CURRENT_A: 6.0,
        CONF_SITE_PHASES: 1,
        CONF_NOMINAL_VOLTAGE: 230.0,
        CONF_DEADBAND_W: 250.0,
        CONF_MIN_DWELL_SECS: 30,
        CONF_GRID_POWER_SENSOR: "sensor.grid_power",
        CONF_SOLAR_POWER_SENSOR: "sensor.solar_power",
        CONF_BATTERY_POWER_SENSOR: "sensor.battery_power",
        CONF_BATTERY_SOC_SENSOR: "sensor.battery_soc",
    }
    entry.options = {}

    coordinator.data = {
        "stations": [
            {
                "id": "CS1",
                "isOnline": True,
                "protocol": "ocpp2.0.1",
                "defaultEvseId": 1,
                "activeTransactionId": "tx-1",
            }
        ]
    }
    coordinator.get_station_protocol.return_value = "ocpp2.0.1"

    client.set_station_limit = AsyncMock()
    hass.services.has_service.return_value = False  # Test REST dispatch path

    controller = CitrineLoadController(
        hass=hass,
        entry=entry,
        coordinator=coordinator,
        client=client,
    )
    return controller, hass, client


@pytest.mark.asyncio
async def test_solar_tracking_charging_allocation(mock_controller_env):
    """Scenario 1: Excess solar charges active EVSE above the start buffer."""
    controller, hass, client = mock_controller_env

    # 4000W solar generation, 1000W grid export (-1000W), 0W battery
    # non_ev_house_w = -1000 + 4000 + 0 - 0 = 3000W house load
    # raw_surplus = 4000 - 3000 = 1000W
    # surplus is above 250W buffer -> available budget = 750W (below 6A/1380W pilot) -> allocates 0W
    def get_state(entity_id):
        state = MagicMock()
        if entity_id == "sensor.solar_power":
            state.state = "4000.0"
        elif entity_id == "sensor.grid_power":
            state.state = "-1000.0"
        elif entity_id == "sensor.battery_power":
            state.state = "0.0"
        elif entity_id == "sensor.battery_soc":
            state.state = "80.0"
        return state

    hass.states.get.side_effect = get_state

    # 1st run: 1000W surplus -> budget 750W (< 1380W min pilot floor) -> 0W
    await controller.async_recompute()
    assert controller.solar_surplus_w == 1000.0
    assert controller.allocated_ev_power_w == 0.0

    # 2nd run: Solar jumps to 6000W, grid export -3000W
    # non_ev_house_w = -3000 + 6000 = 3000W
    # raw surplus = 6000 - 3000 = 3000W
    # budget = 3000 - 250 = 2750W (>= 1380W min pilot) -> allocates 2750W
    def get_higher_solar_state(entity_id):
        state = MagicMock()
        if entity_id == "sensor.solar_power":
            state.state = "6000.0"
        elif entity_id == "sensor.grid_power":
            state.state = "-3000.0"
        elif entity_id == "sensor.battery_power":
            state.state = "0.0"
        elif entity_id == "sensor.battery_soc":
            state.state = "80.0"
        return state

    hass.states.get.side_effect = get_higher_solar_state

    # Force time forward past min dwell time
    controller._last_state_change_time.clear()

    await controller.async_recompute()
    assert controller.solar_surplus_w == 3000.0
    assert controller.allocated_ev_power_w == 2750.0
    client.set_station_limit.assert_called_with(
        protocol="ocpp2.0.1",
        station_id="CS1",
        limit=2750.0,
        unit="W",
        evse_id=1,
        duration=300,
    )


@pytest.mark.asyncio
async def test_sudden_load_spike_curtailment(mock_controller_env):
    """Scenario 2: Sudden home load spike (>92% fuse rating) forces instant curtailment."""
    controller, hass, client = mock_controller_env
    controller.set_mode(MODE_GRID_CAPPED)

    # Main fuse is 14400W. Grid import spikes to 13500W (> 92% of 14400W = 13248W)
    def get_spike_state(entity_id):
        state = MagicMock()
        if entity_id == "sensor.solar_power":
            state.state = "0.0"
        elif entity_id == "sensor.grid_power":
            state.state = "13500.0"
        elif entity_id == "sensor.battery_power":
            state.state = "0.0"
        return state

    hass.states.get.side_effect = get_spike_state

    await controller.async_recompute()
    assert controller.state_status == "Curtailing (Grid Alert)"
