"""Unit tests for the CitrineOS multi-station power allocator."""

import pytest
from custom_components.citrine_ha.allocator import (
    StationLoadContext,
    calculate_station_allocations,
)


def test_empty_stations():
    """Verify empty station list returns empty allocation mapping."""
    allocations = calculate_station_allocations(10000.0, [])
    assert allocations == {}


def test_zero_budget():
    """Verify zero available budget allocates 0W across all stations."""
    st1 = StationLoadContext(station_id="CS1", is_active=True, is_online=True)
    st2 = StationLoadContext(station_id="CS2", is_active=True, is_online=True)
    allocations = calculate_station_allocations(0.0, [st1, st2])
    assert allocations["CS1"] == 0.0
    assert allocations["CS2"] == 0.0


def test_minimum_pilot_current_floor():
    """Verify that if budget is below minimum 6A pilot (1380W), stations receive 0W."""
    st1 = StationLoadContext(
        station_id="CS1",
        is_active=True,
        is_online=True,
        min_current_a=6.0,
        phases=1,
        nominal_voltage=230.0,
    )
    # Budget is 1000W, which is less than min_power_w (1380W)
    allocations = calculate_station_allocations(1000.0, [st1])
    assert allocations["CS1"] == 0.0

    # Budget is 1400W, which satisfies min_power_w (1380W)
    allocations = calculate_station_allocations(1400.0, [st1])
    assert allocations["CS1"] == 1400.0


def test_priority_weighted_allocation():
    """Verify two active stations share surplus power according to priority weights."""
    st_low = StationLoadContext(
        station_id="CS_LOW",
        priority=1,
        is_active=True,
        is_online=True,
        max_limit_w=10000.0,
    )
    st_high = StationLoadContext(
        station_id="CS_HIGH",
        priority=3,
        is_active=True,
        is_online=True,
        max_limit_w=10000.0,
    )

    # Base pilot required for 2 stations = 2 * 1380 = 2760W
    # Total budget = 6760W -> remaining surplus = 4000W
    # High priority gets 3/4 (3000W) + 1380W = 4380W
    # Low priority gets 1/4 (1000W) + 1380W = 2380W
    allocations = calculate_station_allocations(6760.0, [st_low, st_high])
    assert allocations["CS_HIGH"] == 4380.0
    assert allocations["CS_LOW"] == 2380.0


def test_station_overrides():
    """Verify boost and pause override behavior."""
    st_boost = StationLoadContext(
        station_id="CS_BOOST",
        is_active=True,
        is_online=True,
        max_limit_w=7400.0,
        override_mode="boost",
    )
    st_pause = StationLoadContext(
        station_id="CS_PAUSE",
        is_active=True,
        is_online=True,
        override_mode="pause",
    )
    st_auto = StationLoadContext(
        station_id="CS_AUTO",
        is_active=True,
        is_online=True,
        max_limit_w=7400.0,
        override_mode="auto",
    )

    # Budget 10000W: Boost takes 7400W first, remaining 2600W goes to Auto, Pause gets 0W
    allocations = calculate_station_allocations(10000.0, [st_boost, st_pause, st_auto])
    assert allocations["CS_BOOST"] == 7400.0
    assert allocations["CS_PAUSE"] == 0.0
    assert allocations["CS_AUTO"] == 2600.0


def test_three_phase_pilot_floor():
    """Verify 3-phase station requires 3 * 230V * 6A = 4140W minimum pilot power."""
    st_3ph = StationLoadContext(
        station_id="CS_3PH",
        phases=3,
        nominal_voltage=230.0,
        min_current_a=6.0,
        is_active=True,
        is_online=True,
    )
    assert st_3ph.min_power_w == 4140.0

    # Insufficient for 3-phase minimum
    allocations = calculate_station_allocations(3000.0, [st_3ph])
    assert allocations["CS_3PH"] == 0.0

    # Sufficient for 3-phase minimum
    allocations = calculate_station_allocations(5000.0, [st_3ph])
    assert allocations["CS_3PH"] == 5000.0


def test_per_phase_headroom_clamping():
    """Verify single-phase charger on L1 is clamped if Phase A headroom is tight."""
    st_l1 = StationLoadContext(
        station_id="CS_L1",
        phase_connection="1-Phase (L1 / Phase A)",
        phases=1,
        nominal_voltage=230.0,
        min_current_a=6.0,
        max_limit_w=7400.0,
        is_active=True,
        is_online=True,
    )

    # Budget available overall is 7400W (32A), but Phase A headroom is only 10A (2300W)
    phase_headroom = (10.0, 32.0, 32.0)
    allocations = calculate_station_allocations(
        7400.0,
        [st_l1],
        phase_headroom_a=phase_headroom,
    )
    # Target clamped to 10A * 230V = 2300W
    assert allocations["CS_L1"] == 2300.0


def test_phase_unbalance_clamping():
    """Verify 1-phase charger is clamped if charging would exceed max allowed phase unbalance."""
    st_l1 = StationLoadContext(
        station_id="CS_L1",
        phase_connection="1-Phase (L1 / Phase A)",
        phases=1,
        nominal_voltage=230.0,
        min_current_a=6.0,
        max_limit_w=7400.0,
        is_active=True,
        is_online=True,
    )

    # Base house loads: L1 = 15A, L2 = 5A, L3 = 5A (current unbalance = 10A)
    # Max allowed unbalance = 20A -> Max additional current on L1 before unbalance = 15A (3450W)
    base_currents = (15.0, 5.0, 5.0)
    allocations = calculate_station_allocations(
        7400.0,
        [st_l1],
        base_phase_currents_a=base_currents,
        max_phase_unbalance_a=20.0,
    )
    # Clamped to ensure (15 + I_ev) - 5 <= 20 -> I_ev <= 10A -> 2300W + initial headroom
    assert allocations["CS_L1"] <= 4600.0

