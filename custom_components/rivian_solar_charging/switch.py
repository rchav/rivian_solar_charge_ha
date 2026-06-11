"""Switch platform — solar charging enable/disable + charge now override."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_HOME_LAT,
    CONF_HOME_LNG,
    CONF_SCAN_INTERVAL,
    CONF_VEHICLE_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_AMPS,
)
from .coordinator import SolarChargingCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SolarChargingCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        RivianSolarSwitch(coordinator, entry.entry_id),
        RivianChargeNowSwitch(coordinator, entry.entry_id),
    ])


class RivianSolarSwitch(CoordinatorEntity, SwitchEntity):
    """Master toggle — pauses/resumes the solar charging automation."""

    def __init__(self, coordinator: SolarChargingCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_solar_charging_enabled"
        self._attr_name = "Rivian Solar Charging"
        self._attr_icon = "mdi:solar-power"
        self._attr_device_info = coordinator.device_info
        self._enabled = True

    @property
    def is_on(self) -> bool:
        return self._enabled

    async def async_turn_on(self, **kwargs) -> None:
        self._enabled = True
        interval = self.coordinator.config.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        self.coordinator.update_interval = timedelta(seconds=interval)
        self.async_write_ha_state()
        await self.coordinator.async_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Disable automation and stop charging immediately."""
        self._enabled = False
        self.coordinator.charge_now = False
        self.coordinator.update_interval = None
        config = self.coordinator.config
        try:
            await self.coordinator.rivian.set_charging_schedule(
                vehicle_id=config[CONF_VEHICLE_ID],
                amperage=0,
                latitude=config[CONF_HOME_LAT],
                longitude=config[CONF_HOME_LNG],
            )
            self.coordinator._current_amps = 0  # noqa: SLF001
            _LOGGER.info("Solar charging disabled")
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to disable charging: %s", err)
        self.async_write_ha_state()


class RivianChargeNowSwitch(CoordinatorEntity, SwitchEntity):
    """Charge Now — bypass solar logic and charge at full amps immediately.

    Turns itself off automatically when the car reaches its charge limit.
    Can be used to charge from the grid or battery when solar is unavailable.
    """

    def __init__(self, coordinator: SolarChargingCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_charge_now"
        self._attr_name = "Rivian Charge Now"
        self._attr_icon = "mdi:lightning-bolt"
        self._attr_device_info = coordinator.device_info

    @property
    def is_on(self) -> bool:
        return self.coordinator.charge_now

    async def async_turn_on(self, **kwargs) -> None:
        """Start charging at max amps immediately, bypassing solar logic."""
        self.coordinator.charge_now = True
        # Make sure the solar switch is on so polling is active
        interval = self.coordinator.config.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        if self.coordinator.update_interval is None:
            self.coordinator.update_interval = timedelta(seconds=interval)
        _LOGGER.info("Charge Now activated — charging at %dA", MAX_AMPS)
        self.async_write_ha_state()
        await self.coordinator.async_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Cancel Charge Now and return to solar-only mode."""
        self.coordinator.charge_now = False
        _LOGGER.info("Charge Now cancelled — returning to solar mode")
        self.async_write_ha_state()
        await self.coordinator.async_refresh()
