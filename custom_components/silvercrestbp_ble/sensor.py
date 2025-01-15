"""Support for SilvercrestBP sensors."""

from __future__ import annotations

from .silvercrest_bp import SilvercrestBPSensor, SensorUpdate

from homeassistant import config_entries
from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothDataProcessor,
    PassiveBluetoothDataUpdate,
    PassiveBluetoothProcessorCoordinator,
    PassiveBluetoothProcessorEntity,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfPressure,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.sensor import sensor_device_info_to_hass_device_info

from .device import device_key_to_bluetooth_entity_key
from .const import DOMAIN



SENSOR_DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    SilvercrestBPSensor.SYSTOLIC: SensorEntityDescription(
        key=SilvercrestBPSensor.SYSTOLIC,
        native_unit_of_measurement=UnitOfPressure.MMHG,
        device_class=SensorDeviceClass.PRESSURE,
        icon="mdi:water-minus",
    ),
    SilvercrestBPSensor.DIASTOLIC: SensorEntityDescription(
        key=SilvercrestBPSensor.DIASTOLIC,
        native_unit_of_measurement=UnitOfPressure.MMHG,
        device_class=SensorDeviceClass.PRESSURE,
        icon="mdi:water-plus",
    ),
    SilvercrestBPSensor.PULSE: SensorEntityDescription(
        key=SilvercrestBPSensor.PULSE,
        native_unit_of_measurement="bpm",
        icon="mdi:heart-flash",
    ),
    SilvercrestBPSensor.SIGNAL_STRENGTH: SensorEntityDescription(
        key=SilvercrestBPSensor.SIGNAL_STRENGTH,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # SilvercrestBPSensor.BATTERY_PERCENT: SensorEntityDescription(
    #     key=SilvercrestBPSensor.BATTERY_PERCENT,
    #     device_class=SensorDeviceClass.BATTERY,
    #     native_unit_of_measurement=PERCENTAGE,
    #     state_class=SensorStateClass.MEASUREMENT,
    #     entity_category=EntityCategory.DIAGNOSTIC,
    # ),
    SilvercrestBPSensor.TIMESTAMP: SensorEntityDescription(
        key=SilvercrestBPSensor.TIMESTAMP,
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-time-four-outline",
    ),

}


def sensor_update_to_bluetooth_data_update(
    sensor_update: SensorUpdate,
) -> PassiveBluetoothDataUpdate:
    """Convert a sensor update to a bluetooth data update."""
    return PassiveBluetoothDataUpdate(
        devices={
            device_id: sensor_device_info_to_hass_device_info(device_info)
            for device_id, device_info in sensor_update.devices.items()
        },
        entity_descriptions={
            device_key_to_bluetooth_entity_key(device_key): SENSOR_DESCRIPTIONS[
                device_key.key
            ]
            for device_key in sensor_update.entity_descriptions
        },
        entity_data={
            device_key_to_bluetooth_entity_key(device_key): sensor_values.native_value
            for device_key, sensor_values in sensor_update.entity_values.items()
        },
        entity_names={
            device_key_to_bluetooth_entity_key(device_key): sensor_values.name
            for device_key, sensor_values in sensor_update.entity_values.items()
        },
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SilvercrestBP BLE sensors."""
    coordinator: PassiveBluetoothProcessorCoordinator = hass.data[DOMAIN][
        entry.entry_id
    ]
    processor = PassiveBluetoothDataProcessor(sensor_update_to_bluetooth_data_update)
    entry.async_on_unload(
        processor.async_add_entities_listener(
            SilvercrestBPBluetoothSensorEntity, async_add_entities
        )
    )
    entry.async_on_unload(
        coordinator.async_register_processor(processor, SensorEntityDescription)
    )


class SilvercrestBPBluetoothSensorEntity(
    PassiveBluetoothProcessorEntity,
    SensorEntity,
):
    """Representation of a SilvercrestBP sensor."""

    @property
    def native_value(self) -> str | int | None:
        """Return the native value."""
        return self.processor.entity_data.get(self.entity_key)

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        The sensor is only created when the device is seen.

        Since these are sleepy devices which stop broadcasting
        when not in use, we can't rely on the last update time
        so once we have seen the device we always return True.
        """
        return True

    @property
    def assumed_state(self) -> bool:
        """Return True if the device is no longer broadcasting."""
        return not self.processor.available
