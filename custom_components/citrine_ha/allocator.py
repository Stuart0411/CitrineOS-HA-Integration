"""Power and phase allocation algorithms for CitrineOS local load controller."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)


@dataclass
class StationLoadContext:
    """Live state context for a controllable charging station."""

    station_id: str
    evse_id: int = 1
    protocol: str = "ocpp2.0.1"
    is_online: bool = True
    is_active: bool = False  # Plugged in or active transaction
    current_power_w: float = 0.0
    priority: int = 3  # 1 (lowest) to 5 (highest)
    max_limit_w: float = 7400.0  # Station hardware limit (e.g. 32A @ 230V 1-ph or 22kW 3-ph)
    min_current_a: float = 6.0  # Minimum pilot current
    phases: int = 1
    phase_connection: str = "3phase"  # "3phase", "L1", "L2", "L3"
    nominal_voltage: float = 230.0
    override_mode: str = "auto"  # "auto", "boost", "pause"

    @property
    def effective_phases(self) -> int:
        """Return 3 if 3-phase, else 1."""
        return 3 if self.phase_connection in {"3phase", "3-Phase (L1+L2+L3)"} or self.phases == 3 else 1

    @property
    def min_power_w(self) -> float:
        """Calculate minimum power required to charge (pilot floor)."""
        return self.nominal_voltage * self.min_current_a * self.effective_phases

    def power_to_phase_currents(self, power_w: float) -> tuple[float, float, float]:
        """Convert a target power in Watts into per-phase currents (L1, L2, L3) in Amps."""
        if power_w <= 0.0:
            return (0.0, 0.0, 0.0)

        v = max(1.0, self.nominal_voltage)
        if self.effective_phases == 3:
            current_per_phase = power_w / (3.0 * v)
            return (current_per_phase, current_per_phase, current_per_phase)

        single_current = power_w / v
        conn = str(self.phase_connection).upper()
        if "L2" in conn or "PHASE B" in conn:
            return (0.0, single_current, 0.0)
        elif "L3" in conn or "PHASE C" in conn:
            return (0.0, 0.0, single_current)
        else:  # Default to L1 / Phase A
            return (single_current, 0.0, 0.0)


def calculate_station_allocations(
    total_available_power_w: float,
    stations: list[StationLoadContext],
    *,
    phase_headroom_a: tuple[float, float, float] | None = None,
    base_phase_currents_a: tuple[float, float, float] | None = None,
    max_phase_unbalance_a: float | None = None,
) -> dict[str, float]:
    """
    Distribute available power among stations with priority weighting,
    individual phase current limits, and phase-unbalance safety guards.
    """
    allocations: dict[str, float] = {}

    if not stations:
        return allocations

    # Handle explicit overrides first
    controllable_stations: list[StationLoadContext] = []
    budget = max(0.0, float(total_available_power_w))

    for st in stations:
        if not st.is_online or st.override_mode == "pause":
            allocations[st.station_id] = 0.0
        elif st.override_mode == "boost":
            boost_limit = min(st.max_limit_w, budget)
            allocations[st.station_id] = boost_limit
            budget = max(0.0, budget - boost_limit)
        elif st.is_active:
            controllable_stations.append(st)
        else:
            # Station online but no car connected / idle
            allocations[st.station_id] = 0.0

    if not controllable_stations:
        return _apply_phase_constraints(
            allocations,
            stations,
            phase_headroom_a=phase_headroom_a,
            base_phase_currents_a=base_phase_currents_a,
            max_phase_unbalance_a=max_phase_unbalance_a,
        )

    # Sort controllable stations by priority descending (5 -> 1)
    controllable_stations.sort(key=lambda s: s.priority, reverse=True)

    # First pass: Check if budget can satisfy minimum pilot current for priority stations
    remaining_budget = budget
    active_recipients: list[StationLoadContext] = []

    for st in controllable_stations:
        min_p = st.min_power_w
        if remaining_budget >= min_p:
            active_recipients.append(st)
            allocations[st.station_id] = min_p
            remaining_budget -= min_p
        else:
            allocations[st.station_id] = 0.0

    if not active_recipients:
        for st in controllable_stations:
            allocations[st.station_id] = 0.0
        return allocations

    # Second pass: Distribute remaining budget according to priority weight
    total_priority_weight = sum(st.priority for st in active_recipients)
    if total_priority_weight > 0 and remaining_budget > 0:
        for st in active_recipients:
            weight = st.priority / total_priority_weight
            additional_w = remaining_budget * weight
            new_limit = allocations[st.station_id] + additional_w
            allocations[st.station_id] = round(min(st.max_limit_w, new_limit), 1)

    # Apply 3-Phase balancing & individual phase headroom constraints
    return _apply_phase_constraints(
        allocations,
        stations,
        phase_headroom_a=phase_headroom_a,
        base_phase_currents_a=base_phase_currents_a,
        max_phase_unbalance_a=max_phase_unbalance_a,
    )


def _apply_phase_constraints(
    allocations: dict[str, float],
    stations: list[StationLoadContext],
    *,
    phase_headroom_a: tuple[float, float, float] | None,
    base_phase_currents_a: tuple[float, float, float] | None,
    max_phase_unbalance_a: float | None,
) -> dict[str, float]:
    """Clamp station allocations if any phase headroom or unbalance rule is exceeded."""
    if phase_headroom_a is None and max_phase_unbalance_a is None:
        return allocations

    station_map = {st.station_id: st for st in stations}
    constrained_allocations = dict(allocations)

    # 1. Per-Phase Headroom Clamping
    if phase_headroom_a is not None:
        h1, h2, h3 = phase_headroom_a
        for st_id, p_watts in list(constrained_allocations.items()):
            st = station_map.get(st_id)
            if not st or p_watts <= 0.0:
                continue
            i1, i2, i3 = st.power_to_phase_currents(p_watts)
            scale_factors = [1.0]
            if i1 > 0 and h1 > 0 and i1 > h1:
                scale_factors.append(h1 / i1)
            if i2 > 0 and h2 > 0 and i2 > h2:
                scale_factors.append(h2 / i2)
            if i3 > 0 and h3 > 0 and i3 > h3:
                scale_factors.append(h3 / i3)

            min_scale = min(scale_factors)
            if min_scale < 1.0:
                adjusted = round(p_watts * min_scale, 1)
                # If adjusted power falls below minimum pilot, clamp to 0
                constrained_allocations[st_id] = adjusted if adjusted >= st.min_power_w else 0.0

    # 2. Maximum Phase-to-Phase Unbalance Clamping (e.g. AS/NZS 4777 20A limit)
    if max_phase_unbalance_a is not None and base_phase_currents_a is not None and max_phase_unbalance_a > 0:
        b1, b2, b3 = base_phase_currents_a

        # Calculate combined total phase currents
        tot1 = b1 + sum(station_map[s].power_to_phase_currents(w)[0] for s, w in constrained_allocations.items() if s in station_map)
        tot2 = b2 + sum(station_map[s].power_to_phase_currents(w)[1] for s, w in constrained_allocations.items() if s in station_map)
        tot3 = b3 + sum(station_map[s].power_to_phase_currents(w)[2] for s, w in constrained_allocations.items() if s in station_map)

        max_tot = max(tot1, tot2, tot3)
        min_tot = min(tot1, tot2, tot3)

        if (max_tot - min_tot) > max_phase_unbalance_a:
            # Scale down 1-phase chargers on the highest loaded phase
            highest_phase_idx = [tot1, tot2, tot3].index(max_tot)  # 0: L1, 1: L2, 2: L3
            excess_a = (max_tot - min_tot) - max_phase_unbalance_a

            for st_id, p_watts in list(constrained_allocations.items()):
                st = station_map.get(st_id)
                if not st or st.effective_phases != 1 or p_watts <= 0.0:
                    continue
                currs = st.power_to_phase_currents(p_watts)
                if currs[highest_phase_idx] > 0:
                    reduction_w = excess_a * st.nominal_voltage
                    adjusted_w = round(max(0.0, p_watts - reduction_w), 1)
                    constrained_allocations[st_id] = adjusted_w if adjusted_w >= st.min_power_w else 0.0

    return constrained_allocations

