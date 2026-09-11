"""Power allocation algorithms for CitrineOS local load controller."""

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
    max_limit_w: float = 7400.0  # Station hardware limit (e.g. 32A @ 230V 1-ph)
    min_current_a: float = 6.0  # Minimum pilot current
    phases: int = 1
    nominal_voltage: float = 230.0
    override_mode: str = "auto"  # "auto", "boost", "pause"

    @property
    def min_power_w(self) -> float:
        """Calculate minimum power required to charge (pilot floor)."""
        return self.nominal_voltage * self.min_current_a * max(1, self.phases)


def calculate_station_allocations(
    total_available_power_w: float,
    stations: list[StationLoadContext],
) -> dict[str, float]:
    """
    Distribute available power among stations.
    Returns mapping of station_id -> target power in Watts.
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
        return allocations

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
        # Cannot satisfy even a single station's minimum pilot current
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
            # Cap at station maximum
            allocations[st.station_id] = round(min(st.max_limit_w, new_limit), 1)

    return allocations
