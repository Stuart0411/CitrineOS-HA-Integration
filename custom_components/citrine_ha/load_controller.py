"""Local Load Controller for CitrineOS and Home Assistant."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from datetime import timedelta

from .allocator import StationLoadContext, calculate_station_allocations
from .citrine_api import CitrineApiError, CitrineClient
from .const import (
    CONF_BATTERY_MIN_SOC,
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_CONTROLLER_INTERVAL_SECS,
    CONF_CONTROLLER_MODE,
    CONF_DEADBAND_W,
    CONF_GRID_POWER_SENSOR,
    CONF_MAIN_FUSE_LIMIT_W,
    CONF_MIN_CHARGE_CURRENT_A,
    CONF_MIN_DWELL_SECS,
    CONF_MQTT_TOPIC_PREFIX,
    CONF_NOMINAL_VOLTAGE,
    CONF_RAMP_RATE_W_S,
    CONF_SITE_EXPORT_LIMIT_W,
    CONF_SITE_ID,
    CONF_SITE_PHASES,
    CONF_SOLAR_POWER_SENSOR,
    CONF_SOLAR_START_BUFFER_W,
    DEFAULT_BATTERY_MIN_SOC,
    DEFAULT_CONTROLLER_INTERVAL_SECS,
    DEFAULT_CONTROLLER_MODE,
    DEFAULT_DEADBAND_W,
    DEFAULT_MAIN_FUSE_LIMIT_W,
    DEFAULT_MIN_CHARGE_CURRENT_A,
    DEFAULT_MIN_DWELL_SECS,
    DEFAULT_MQTT_TOPIC_PREFIX,
    DEFAULT_NOMINAL_VOLTAGE,
    DEFAULT_RAMP_RATE_W_S,
    DEFAULT_SITE_EXPORT_LIMIT_W,
    DEFAULT_SITE_ID,
    DEFAULT_SITE_PHASES,
    DEFAULT_SOLAR_START_BUFFER_W,
    MODE_DYNAMIC_DOE,
    MODE_EMERGENCY_SAFE,
    MODE_GRID_CAPPED,
    MODE_OFF,
    MODE_SOLAR_BATTERY,
    MODE_SOLAR_ONLY,
)
from .coordinator import CitrineCoordinator
from .mqtt_intent import MqttIntentPublisher

_LOGGER = logging.getLogger(__name__)


class CitrineLoadController:
    """Intelligent real-time site load controller."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: CitrineCoordinator,
        client: CitrineClient,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.client = client

        site_id = str(
            entry.options.get(CONF_SITE_ID)
            or entry.data.get(CONF_SITE_ID, DEFAULT_SITE_ID)
        )
        topic_prefix = str(
            entry.options.get(CONF_MQTT_TOPIC_PREFIX)
            or entry.data.get(CONF_MQTT_TOPIC_PREFIX, DEFAULT_MQTT_TOPIC_PREFIX)
        )
        self.mqtt_publisher = MqttIntentPublisher(
            hass,
            site_id=site_id,
            topic_prefix=topic_prefix,
            ttl_seconds=60,
        )

        # Dynamic State Variables
        self.mode = str(entry.options.get(CONF_CONTROLLER_MODE) or entry.data.get(CONF_CONTROLLER_MODE) or DEFAULT_CONTROLLER_MODE)
        self.state_status = "Initialized"
        self.site_headroom_w = 0.0
        self.allocated_ev_power_w = 0.0
        self.solar_surplus_w = 0.0
        self.active_ev_count = 0
        self.last_run_timestamp: str | None = None
        self.last_error: str | None = None

        # Tracking per station
        self._last_applied_limits: dict[str, float] = {}
        self._last_state_change_time: dict[str, datetime] = {}
        self._station_overrides: dict[str, str] = {}  # "auto", "boost", "pause"
        self._station_priorities: dict[str, int] = {}  # 1-5

        self._unsub_timer: CALLBACK_TYPE | None = None
        self._unsub_trackers: list[CALLBACK_TYPE] = []
        self._lock = asyncio.Lock()

    async def async_start(self) -> None:
        """Start the controller loop and sensor listeners."""
        interval = int(
            self.entry.options.get(CONF_CONTROLLER_INTERVAL_SECS)
            or self.entry.data.get(CONF_CONTROLLER_INTERVAL_SECS, DEFAULT_CONTROLLER_INTERVAL_SECS)
        )
        self._unsub_timer = async_track_time_interval(
            self.hass,
            self._async_timer_tick,
            timedelta(seconds=max(1, interval)),
        )

        # Track reactive state changes on key sensors for immediate step-response
        tracked_sensors = [
            self.entry.options.get(CONF_GRID_POWER_SENSOR) or self.entry.data.get(CONF_GRID_POWER_SENSOR),
            self.entry.options.get(CONF_SOLAR_POWER_SENSOR) or self.entry.data.get(CONF_SOLAR_POWER_SENSOR),
        ]
        valid_sensors = [str(s) for s in tracked_sensors if s and str(s).strip()]
        if valid_sensors:
            self._unsub_trackers.append(
                async_track_state_change_event(self.hass, valid_sensors, self._async_sensor_event)
            )

        _LOGGER.info("CitrineOS Load Controller started with interval %ss in mode %s", interval, self.mode)

    async def async_stop(self) -> None:
        """Stop the controller loop and listeners."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        for unsub in self._unsub_trackers:
            unsub()
        self._unsub_trackers.clear()
        _LOGGER.info("CitrineOS Load Controller stopped.")

    @callback
    def _async_timer_tick(self, now: datetime) -> None:
        self.hass.async_create_task(self.async_recompute())

    @callback
    def _async_sensor_event(self, event: Event) -> None:
        # Trigger immediate recompute if grid changes significantly
        self.hass.async_create_task(self.async_recompute())

    def set_mode(self, new_mode: str) -> None:
        """Change operating mode."""
        self.mode = new_mode
        _LOGGER.info("Citrine Load Controller mode set to: %s", new_mode)
        self.hass.async_create_task(self.async_recompute())

    def set_station_override(self, station_id: str, override: str) -> None:
        """Set station override mode ('auto', 'boost', 'pause')."""
        self._station_overrides[station_id] = override
        self.hass.async_create_task(self.async_recompute())

    def set_station_priority(self, station_id: str, priority: int) -> None:
        """Set station priority (1 to 5)."""
        self._station_priorities[station_id] = max(1, min(int(priority), 5))
        self.hass.async_create_task(self.async_recompute())

    async def async_recompute(self) -> None:
        """Main control loop calculation & execution."""
        if self._lock.locked():
            return

        async with self._lock:
            try:
                await self._execute_control_cycle()
            except Exception as err:  # noqa: BLE001
                self.last_error = str(err)
                self.state_status = "Error"
                _LOGGER.error("Error in Citrine load control cycle: %s", err, exc_info=True)

    async def _execute_control_cycle(self) -> None:
        now = datetime.now(UTC)
        self.last_run_timestamp = now.isoformat()

        # Configured thresholds
        main_fuse_w = float(
            self.entry.options.get(CONF_MAIN_FUSE_LIMIT_W)
            or self.entry.data.get(CONF_MAIN_FUSE_LIMIT_W, DEFAULT_MAIN_FUSE_LIMIT_W)
        )
        export_limit_w = float(
            self.entry.options.get(CONF_SITE_EXPORT_LIMIT_W)
            or self.entry.data.get(CONF_SITE_EXPORT_LIMIT_W, DEFAULT_SITE_EXPORT_LIMIT_W)
        )
        solar_buffer_w = float(
            self.entry.options.get(CONF_SOLAR_START_BUFFER_W)
            or self.entry.data.get(CONF_SOLAR_START_BUFFER_W, DEFAULT_SOLAR_START_BUFFER_W)
        )
        min_current_a = float(
            self.entry.options.get(CONF_MIN_CHARGE_CURRENT_A)
            or self.entry.data.get(CONF_MIN_CHARGE_CURRENT_A, DEFAULT_MIN_CHARGE_CURRENT_A)
        )
        phases = int(
            self.entry.options.get(CONF_SITE_PHASES)
            or self.entry.data.get(CONF_SITE_PHASES, DEFAULT_SITE_PHASES)
        )
        voltage = float(
            self.entry.options.get(CONF_NOMINAL_VOLTAGE)
            or self.entry.data.get(CONF_NOMINAL_VOLTAGE, DEFAULT_NOMINAL_VOLTAGE)
        )
        deadband_w = float(
            self.entry.options.get(CONF_DEADBAND_W)
            or self.entry.data.get(CONF_DEADBAND_W, DEFAULT_DEADBAND_W)
        )
        min_dwell = int(
            self.entry.options.get(CONF_MIN_DWELL_SECS)
            or self.entry.data.get(CONF_MIN_DWELL_SECS, DEFAULT_MIN_DWELL_SECS)
        )

        # Telemetry ingestion
        grid_w = self._read_sensor_value(CONF_GRID_POWER_SENSOR, 0.0)
        solar_w = max(0.0, self._read_sensor_value(CONF_SOLAR_POWER_SENSOR, 0.0))
        battery_w = self._read_sensor_value(CONF_BATTERY_POWER_SENSOR, 0.0)
        battery_soc = self._read_sensor_value(CONF_BATTERY_SOC_SENSOR, 100.0)
        min_soc = float(
            self.entry.options.get(CONF_BATTERY_MIN_SOC)
            or self.entry.data.get(CONF_BATTERY_MIN_SOC, DEFAULT_BATTERY_MIN_SOC)
        )

        # Build station load contexts
        station_contexts: list[StationLoadContext] = []
        total_ev_current_power = 0.0

        for st in self.coordinator.data.get("stations", []):
            st_id = str(st.get("id"))
            is_online = bool(st.get("isOnline"))
            has_tx = bool(
                st.get("activeTransactionId")
                or st.get("currentTransactionId")
                or st.get("transactionId")
            )
            # Estimate or read live station power
            st_power = float(st.get("activePowerW", self._last_applied_limits.get(st_id, 0.0) if has_tx else 0.0))
            if has_tx and is_online:
                total_ev_current_power += st_power

            override = self._station_overrides.get(st_id, "auto")
            priority = self._station_priorities.get(st_id, 3)

            station_contexts.append(
                StationLoadContext(
                    station_id=st_id,
                    evse_id=int(st.get("defaultEvseId", 1)),
                    protocol=str(st.get("protocol", "ocpp2.0.1")),
                    is_online=is_online,
                    is_active=has_tx or override == "boost",
                    current_power_w=st_power,
                    priority=priority,
                    min_current_a=min_current_a,
                    phases=phases,
                    nominal_voltage=voltage,
                    override_mode=override,
                )
            )

        self.active_ev_count = sum(1 for st in station_contexts if st.is_active and st.is_online)

        # Non-EV house loads calculation
        # grid_w > 0 is importing, grid_w < 0 is exporting
        # house_loads = grid_import + solar_gen + battery_discharge - ev_power
        non_ev_house_w = max(0.0, grid_w + solar_w + battery_w - total_ev_current_power)
        self.site_headroom_w = max(0.0, main_fuse_w - (grid_w - total_ev_current_power))

        # Solar Surplus Calculation
        # Raw surplus = solar_w - non_ev_house_w
        raw_surplus_w = max(0.0, solar_w - non_ev_house_w)
        self.solar_surplus_w = raw_surplus_w

        # Compute Total Budget based on Mode
        if self.mode == MODE_OFF or self.mode == MODE_EMERGENCY_SAFE:
            total_budget_w = 0.0
            self.state_status = "Off" if self.mode == MODE_OFF else "Emergency Safe"

        elif self.mode == MODE_SOLAR_ONLY:
            if raw_surplus_w > solar_buffer_w:
                total_budget_w = raw_surplus_w - solar_buffer_w
                self.state_status = "Tracking Solar"
            else:
                total_budget_w = 0.0
                self.state_status = "Insufficient Solar"

        elif self.mode == MODE_SOLAR_BATTERY:
            allowed_battery_discharge = max(0.0, battery_w) if battery_soc > min_soc else 0.0
            available_green = raw_surplus_w + allowed_battery_discharge
            if available_green > solar_buffer_w:
                total_budget_w = available_green - solar_buffer_w
                self.state_status = "Solar + Battery Active"
            else:
                total_budget_w = 0.0
                self.state_status = "Low Battery / Solar"

        elif self.mode == MODE_GRID_CAPPED:
            total_budget_w = max(0.0, main_fuse_w - non_ev_house_w)
            self.state_status = "Grid Capped (Fast)"

        elif self.mode == MODE_DYNAMIC_DOE:
            # Enforce dynamic import cap
            total_budget_w = max(0.0, min(export_limit_w, main_fuse_w - non_ev_house_w))
            self.state_status = "Dynamic DOE"

        else:
            total_budget_w = 0.0
            self.state_status = "Unknown Mode"

        # Hard safety clamp: Never exceed site fuse headroom
        total_budget_w = min(total_budget_w, self.site_headroom_w)

        # Fast Curtailment Check: If grid import is close to fuse limit (>92%), force fast curtail
        if grid_w > (main_fuse_w * 0.92):
            self.state_status = "Curtailing (Grid Alert)"
            total_budget_w = max(0.0, total_budget_w * 0.5)

        # Calculate per-station target allocations
        allocations = calculate_station_allocations(total_budget_w, station_contexts)
        self.allocated_ev_power_w = sum(allocations.values())

        # Publish structured real-time envelope to CitrineOS MQTT intent bus
        allocation_payloads = [
            {
                "stationId": st_ctx.station_id,
                "evseId": st_ctx.evse_id,
                "protocol": st_ctx.protocol,
                "chargeLimitW": round(allocations.get(st_ctx.station_id, 0.0), 1),
                "dischargeLimitW": 0.0,
                "priority": st_ctx.priority,
                "phases": st_ctx.phases,
                "overrideMode": st_ctx.override_mode,
                "unit": "W",
            }
            for st_ctx in station_contexts
        ]
        await self.mqtt_publisher.async_publish_intent(
            operation_mode=self.mode,
            reason=self.state_status,
            max_import_power_w=main_fuse_w,
            max_export_power_w=export_limit_w,
            allocated_ev_power_w=self.allocated_ev_power_w,
            solar_surplus_w=self.solar_surplus_w,
            site_headroom_w=self.site_headroom_w,
            allocations=allocation_payloads,
            fallback_limit_w=min_current_a * voltage * phases,
        )

        # Apply limits with deadbands, dwell time protection, and rate limiting
        for st_ctx in station_contexts:
            target_limit = allocations.get(st_ctx.station_id, 0.0)
            prev_limit = self._last_applied_limits.get(st_ctx.station_id, 0.0)

            # Check if station state (0W vs >0W) is changing
            state_changing = (prev_limit == 0.0 and target_limit > 0.0) or (prev_limit > 0.0 and target_limit == 0.0)

            if state_changing:
                last_change = self._last_state_change_time.get(st_ctx.station_id)
                if last_change and (now - last_change).total_seconds() < min_dwell:
                    # Dwell time protection active, keep previous state
                    _LOGGER.debug(
                        "Dwell protection active for %s (elapsed %ss < %ss)",
                        st_ctx.station_id,
                        (now - last_change).total_seconds(),
                        min_dwell,
                    )
                    continue
                self._last_state_change_time[st_ctx.station_id] = now

            # Deadband check (if not turning ON or OFF)
            if not state_changing and abs(target_limit - prev_limit) < deadband_w:
                continue

            # Push limit to CitrineOS
            await self._apply_station_limit(st_ctx, target_limit)
            self._last_applied_limits[st_ctx.station_id] = target_limit

        self.last_error = None

    async def _apply_station_limit(self, st_ctx: StationLoadContext, limit_w: float) -> None:
        """Send charging limit to CitrineOS for a station."""
        protocol = self.coordinator.get_station_protocol(st_ctx.station_id, st_ctx.protocol) or "ocpp2.0.1"
        try:
            _LOGGER.info(
                "Dispatching load limit: station=%s protocol=%s limit=%.1fW",
                st_ctx.station_id,
                protocol,
                limit_w,
            )
            await self.client.set_station_limit(
                protocol=protocol,
                station_id=st_ctx.station_id,
                limit=limit_w,
                unit="W",
                evse_id=st_ctx.evse_id,
                duration=300,
            )
        except CitrineApiError as err:
            _LOGGER.warning(
                "Failed to apply load limit %.1fW to station %s: %s",
                limit_w,
                st_ctx.station_id,
                err,
            )
            self.last_error = f"Limit failed on {st_ctx.station_id}: {err}"

    def _read_sensor_value(self, config_key: str, default: float = 0.0) -> float:
        entity_id = self.entry.options.get(config_key) or self.entry.data.get(config_key)
        if not entity_id or not str(entity_id).strip():
            return default

        state = self.hass.states.get(str(entity_id).strip())
        if state is None or state.state in {"unknown", "unavailable", ""}:
            return default

        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default
