"""Sensor platform for Rivian Solar Charging."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfLength, UnitOfPower, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SolarChargingCoordinator


@dataclass
class RivianSensorDescription(SensorEntityDescription):
    """Extended sensor description with a coordinator data key."""
    data_key: str = ""


SENSOR_DESCRIPTIONS: tuple[RivianSensorDescription, ...] = (
    RivianSensorDescription(
        key="charging_state",
        data_key="charging_state",
        name="Solar Charging State",
        icon="mdi:state-machine",
    ),
    RivianSensorDescription(
        key="target_amps",
        data_key="target_amps",
        name="Rivian Target Charge Amps",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
    ),
    RivianSensorDescription(
        key="export_watts",
        data_key="export_watts",
        name="Solar Export Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
    ),
    RivianSensorDescription(
        key="powerwall_charging_watts",
        data_key="powerwall_charging_watts",
        name="Powerwall Charging Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-charging",
    ),
    RivianSensorDescription(
        key="available_watts",
        data_key="available_watts",
        name="Solar Available For Charging",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power-variant",
    ),
    RivianSensorDescription(
        key="battery_level",
        data_key="battery_level",
        name="Rivian Battery Level",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:car-electric",
    ),
    RivianSensorDescription(
        key="charger_state",
        data_key="charger_state",
        name="Rivian Charger State",
        icon="mdi:ev-plug-type2",
    ),
    RivianSensorDescription(
        key="plugged_in",
        data_key="plugged_in",
        name="Rivian Plugged In",
        icon="mdi:power-plug",
    ),
    RivianSensorDescription(
        key="vehicle_at_home",
        data_key="vehicle_at_home",
        name="Rivian At Home",
        icon="mdi:home-map-marker",
    ),
    RivianSensorDescription(
        key="distance_km",
        data_key="distance_km",
        name="Rivian Distance From Home",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:map-marker-distance",
    ),
    RivianSensorDescription(
        key="powerwall_pct",
        data_key="powerwall_pct",
        name="Powerwall State of Charge",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-charging",
    ),
    RivianSensorDescription(
        key="after_sunset",
        data_key="after_sunset",
        name="After Sunset Cutoff",
        icon="mdi:weather-sunset",
    ),
    RivianSensorDescription(
        key="skip_reason",
        data_key="skip_reason",
        name="Solar Charging Skip Reason",
        icon="mdi:pause-circle",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SolarChargingCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RivianSolarSensor(coordinator, desc, entry.entry_id)
        for desc in SENSOR_DESCRIPTIONS
    )


class RivianSolarSensor(CoordinatorEntity, SensorEntity):
    """A sensor backed by the SolarChargingCoordinator."""

    entity_description: RivianSensorDescription

    def __init__(
        self,
        coordinator: SolarChargingCoordinator,
        description: RivianSensorDescription,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self.entity_description.data_key)

# Note: charge_now sensor appended — shows current charge now state
SENSOR_DESCRIPTIONS = SENSOR_DESCRIPTIONS + (
    RivianSensorDescription(
        key="charge_now",
        data_key="charge_now",
        name="Rivian Charge Now Active",
        icon="mdi:lightning-bolt",
    ),
)
