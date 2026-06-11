"""Solar charging coordinator.

Three charging modes:
  CHARGE_NOW — ignore all solar/PW logic, charge at MAX_AMPS immediately.
               Auto-cancels when battery_level >= battery_limit.
  SOLAR_ONLY — normal operation: divert excess solar after PW is full.
  OFF        — automation disabled (switch turned off).

Solar logic state machine (SOLAR_ONLY mode):
  IDLE      — waiting for PW >= start threshold
  ACTIVE    — diverting solar surplus to car
  RAMPDOWN  — PW dropped below stop threshold, stepping down gracefully
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CHARGE_NOW_KEY,
    CONF_BATTERY_LIMIT,
    CONF_CHARGE_LIMIT,
    CONF_GRID_POWER_ENTITY,
    CONF_HOME_LAT,
    CONF_HOME_LNG,
    CONF_POWERWALL_ENTITY,
    CONF_POWERWALL_STOP_PCT,
    CONF_RIVIAN_START_LIMIT,
    CONF_SCAN_INTERVAL,
    CONF_VEHICLE_ID,
    DEADBAND_AMPS,
    DEFAULT_CHARGE_LIMIT,
    DEFAULT_POWERWALL_MIN_PCT,
    DEFAULT_POWERWALL_STOP_PCT,
    DEFAULT_RIVIAN_START_LIMIT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HOME_RADIUS_KM,
    MAX_AMPS,
    MIN_AMPS,
    SUNSET_CUTOFF_MINUTES,
    VOLTAGE,
)
from .rivian_client import RivianClient

_LOGGER = logging.getLogger(__name__)


class ChargingState(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    RAMPDOWN = "rampdown"
    CHARGE_NOW = "charge_now"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class SolarChargingCoordinator(DataUpdateCoordinator):
    """Coordinator that polls HA state + Rivian API and updates the schedule."""

    def __init__(
        self,
        hass: HomeAssistant,
        rivian: RivianClient,
        config: dict[str, Any],
    ) -> None:
        self.rivian = rivian
        self.config = config
        self._current_amps: int = 0
        self._charging_state: ChargingState = ChargingState.IDLE
        self.charge_now: bool = False  # set by the Charge Now switch
        self._schedule_initialized: bool = False  # have we ever asserted control of the schedule?

        interval_seconds = config.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval_seconds),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_after_sunset_cutoff(self) -> bool:
        try:
            sunset = get_astral_event_date(self.hass, "sunset")
            if sunset is None:
                return False
            now = datetime.now(tz=sunset.tzinfo)
            return now >= (sunset - timedelta(minutes=SUNSET_CUTOFF_MINUTES))
        except Exception:  # noqa: BLE001
            return False

    def _get_vehicle_location(self, vstate: dict) -> tuple[bool, float | None]:
        gnss = vstate.get("gnssLocation")
        if not gnss or not gnss.get("latitude") or not gnss.get("longitude"):
            return True, None
        try:
            dist = _haversine_km(
                self.config[CONF_HOME_LAT], self.config[CONF_HOME_LNG],
                float(gnss["latitude"]), float(gnss["longitude"]),
            )
            return dist <= HOME_RADIUS_KM, dist
        except (TypeError, ValueError):
            return True, None

    def _clamp_amps(self, amps: int) -> int:
        if amps <= 0:
            return 0
        return max(MIN_AMPS, min(MAX_AMPS, amps))

    # ------------------------------------------------------------------
    # Main update loop
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        # --- 1. Read Powerwall % ---
        pw_entity = self.config[CONF_POWERWALL_ENTITY]
        pw_state = self.hass.states.get(pw_entity)
        if pw_state is None:
            raise UpdateFailed(f"Entity {pw_entity} not found")
        try:
            powerwall_pct = float(pw_state.state)
        except ValueError as err:
            raise UpdateFailed(f"Cannot parse {pw_entity}: {pw_state.state}") from err

        # --- 2. Read grid power (negative = exporting) ---
        grid_entity = self.config[CONF_GRID_POWER_ENTITY]
        grid_state = self.hass.states.get(grid_entity)
        if grid_state is None:
            raise UpdateFailed(f"Entity {grid_entity} not found")
        try:
            grid_watts = float(grid_state.state)
        except ValueError as err:
            raise UpdateFailed(f"Cannot parse {grid_entity}: {grid_state.state}") from err

        export_watts = -grid_watts

        # --- 3. Read Rivian vehicle state ---
        vehicle_id = self.config[CONF_VEHICLE_ID]
        try:
            vstate = await self.rivian.get_vehicle_state(vehicle_id)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Rivian API error: {err}") from err

        charger_status = (vstate.get("chargerStatus") or {}).get("value", "")
        charger_state = (vstate.get("chargerState") or {}).get("value", "")
        battery_level = float((vstate.get("batteryLevel") or {}).get("value", 0))
        battery_limit = float(
            (vstate.get("batteryLimit") or {}).get(
                "value", self.config.get(CONF_CHARGE_LIMIT, DEFAULT_CHARGE_LIMIT)
            )
        )

        plugged_in = charger_status not in ("chrgr_sts_not_connected", "", None)
        vehicle_at_home, distance_km = self._get_vehicle_location(vstate)

        pw_start_pct = self.config.get(CONF_BATTERY_LIMIT, DEFAULT_POWERWALL_MIN_PCT)
        pw_stop_pct = self.config.get(CONF_POWERWALL_STOP_PCT, DEFAULT_POWERWALL_STOP_PCT)
        rivian_start_limit = self.config.get(CONF_RIVIAN_START_LIMIT, DEFAULT_RIVIAN_START_LIMIT)
        after_sunset = self._is_after_sunset_cutoff()

        _LOGGER.debug(
            "Poll — PW: %.0f%% | Grid: %+.0fW | Rivian: soc=%.1f%% plugged=%s "
            "at_home=%s dist=%.2fkm | sunset=%s charge_now=%s | state=%s amps=%dA",
            powerwall_pct, grid_watts, battery_level, plugged_in,
            vehicle_at_home, distance_km or 0,
            after_sunset, self.charge_now,
            self._charging_state.value, self._current_amps,
        )

        new_amps = self._current_amps
        skip_reason: str | None = None

        # --- 4. CHARGE NOW mode ---
        if self.charge_now:
            if not plugged_in:
                _LOGGER.info("Charge Now: car not plugged in — cancelling")
                self.charge_now = False
                self._charging_state = ChargingState.IDLE
                new_amps = 0
            elif battery_level >= battery_limit:
                _LOGGER.info(
                    "Charge Now: battery %.1f%% reached limit %.0f%% — cancelling",
                    battery_level, battery_limit,
                )
                self.charge_now = False
                self._charging_state = ChargingState.IDLE
                new_amps = 0
            else:
                self._charging_state = ChargingState.CHARGE_NOW
                new_amps = MAX_AMPS
                _LOGGER.debug("Charge Now active — holding at %dA", MAX_AMPS)

        # --- 5. Hard stops (solar mode only) ---
        elif not vehicle_at_home:
            skip_reason = "away_from_home"
        elif after_sunset and self._current_amps > 0:
            skip_reason = "after_sunset_cutoff"
            new_amps = 0
            self._charging_state = ChargingState.IDLE
        elif not plugged_in and self._current_amps > 0:
            skip_reason = "not_plugged_in"
            new_amps = 0
            self._charging_state = ChargingState.IDLE
        elif battery_level >= battery_limit and self._current_amps > 0:
            skip_reason = "rivian_at_charge_limit"
            new_amps = 0
            self._charging_state = ChargingState.IDLE

        # --- 6. Solar state machine ---
        elif not skip_reason:
            if self._charging_state == ChargingState.IDLE:
                if (
                    powerwall_pct >= pw_start_pct
                    and battery_level < rivian_start_limit
                    and plugged_in
                    and not after_sunset
                ):
                    _LOGGER.info(
                        "PW %.0f%% >= %.0f%% — starting solar charging",
                        powerwall_pct, pw_start_pct,
                    )
                    self._charging_state = ChargingState.ACTIVE
                    new_amps = self._clamp_amps(math.floor(export_watts / VOLTAGE))
                else:
                    skip_reason = skip_reason or "waiting_for_solar"

            elif self._charging_state == ChargingState.ACTIVE:
                if powerwall_pct < pw_stop_pct:
                    _LOGGER.info(
                        "PW dropped to %.0f%% < %.0f%% — ramp down",
                        powerwall_pct, pw_stop_pct,
                    )
                    self._charging_state = ChargingState.RAMPDOWN
                    new_amps = self._clamp_amps(self._current_amps - MIN_AMPS)
                else:
                    delta = math.floor(export_watts / VOLTAGE)
                    if abs(delta) <= DEADBAND_AMPS:
                        delta = 0
                    new_amps = self._clamp_amps(self._current_amps + delta)

            elif self._charging_state == ChargingState.RAMPDOWN:
                if powerwall_pct >= pw_start_pct:
                    _LOGGER.info("PW recovered to %.0f%% — resuming", powerwall_pct)
                    self._charging_state = ChargingState.ACTIVE
                    new_amps = self._clamp_amps(math.floor(export_watts / VOLTAGE))
                elif self._current_amps > 0:
                    new_amps = self._clamp_amps(self._current_amps - MIN_AMPS)
                    if new_amps == 0:
                        _LOGGER.info("Ramp-down complete")
                        self._charging_state = ChargingState.IDLE

        # --- 7. Apply if changed ---
        # Note: deliberately NOT gated on `not skip_reason` — the hard-stop
        # branches above (after_sunset_cutoff, not_plugged_in,
        # rivian_at_charge_limit) set skip_reason *and* new_amps=0 to signal
        # "actively stop charging now". The skip-with-no-op branches
        # (waiting_for_solar, away_from_home) never change new_amps, so they
        # remain no-ops here regardless.
        needs_apply = new_amps != self._current_amps
        if (
            not needs_apply
            and not self._schedule_initialized
            and vehicle_at_home
            and not self.charge_now
        ):
            # First run while the car is at home: assert control of the
            # schedule even if the computed target matches the default 0A
            # state and even if the car isn't plugged in yet. The Rivian
            # schedule persists independent of plug state, so if we don't
            # write it now, the car will honor its stale (often full-speed)
            # schedule the instant it gets plugged in.
            needs_apply = True

        if needs_apply:
            await self._apply_amps(vehicle_id, new_amps)

        return {
            "powerwall_pct": powerwall_pct,
            "grid_watts": grid_watts,
            "export_watts": export_watts,
            "plugged_in": plugged_in,
            "battery_level": battery_level,
            "battery_limit": battery_limit,
            "charger_state": charger_state,
            "vehicle_at_home": vehicle_at_home,
            "distance_km": distance_km,
            "target_amps": self._current_amps,
            "charging_state": self._charging_state.value,
            "after_sunset": after_sunset,
            "skip_reason": skip_reason,
            "charge_now": self.charge_now,
        }

    async def _apply_amps(self, vehicle_id: str, amps: int) -> None:
        try:
            success = await self.rivian.set_charging_schedule(
                vehicle_id=vehicle_id,
                amperage=amps,
                latitude=self.config[CONF_HOME_LAT],
                longitude=self.config[CONF_HOME_LNG],
            )
            if success:
                _LOGGER.info("Schedule updated: %dA → %dA", self._current_amps, amps)
                self._current_amps = amps
                self._schedule_initialized = True
            else:
                _LOGGER.warning("set_charging_schedule returned failure")
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to update Rivian schedule: %s", err)
