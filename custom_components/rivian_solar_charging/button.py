"""Button platform for Rivian Solar Charging — force an immediate full sync."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SolarChargingCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SolarChargingCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RivianForceSyncButton(coordinator, entry.entry_id)])


class RivianForceSyncButton(CoordinatorEntity, ButtonEntity):
    """Forces an immediate poll + charge-amps recalculation.

    Bypasses the configured scan interval — same update the coordinator
    would run on its own schedule, just triggered right now.
    """

    def __init__(self, coordinator: SolarChargingCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_force_sync"
        self._attr_has_entity_name = True
        self._attr_name = "Force Sync"
        self._attr_icon = "mdi:sync"
        self._attr_device_info = coordinator.device_info

    async def async_press(self) -> None:
        await self.coordinator.async_refresh()
