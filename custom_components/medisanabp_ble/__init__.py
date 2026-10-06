"""The MedisanaBP integration."""

from __future__ import annotations

import logging


from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
)
from homeassistant.components.bluetooth.active_update_processor import (
    ActiveBluetoothProcessorCoordinator,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CoreState, HomeAssistant

from .medisana_bp import MedisanaBPBluetoothDeviceData, SensorUpdate
from .const import DOMAIN

PLATFORMS: list[Platform] = [Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MedisanaBP BLE device from a config entry."""
    address = entry.unique_id
    assert address is not None
    data = MedisanaBPBluetoothDeviceData()

    def _clear_advertisement_history() -> None:
        # The device sends the same advertisement every time it wakes up after a
        # measurement. The Bluetooth manager only forwards changed advertisements,
        # so without this only the first measurement after a longer pause would
        # trigger a poll. The helper exists since Home Assistant 2026.5.
        if clear := getattr(bluetooth, "async_clear_advertisement_history", None):
            clear(hass, address)

    def _needs_poll(
        service_info: BluetoothServiceInfoBleak, last_poll: float | None
    ) -> bool:
        # Only poll if hass is running, we need to poll,
        # and we actually have a way to connect to the device
        needs_poll = (
            hass.state is CoreState.running
            and data.poll_needed(service_info, last_poll)
            and bool(
                service_info.connectable
                or async_ble_device_from_address(
                    hass, service_info.device.address, connectable=True
                )
            )
        )
        if not needs_poll:
            _clear_advertisement_history()
        return needs_poll

    async def _async_poll(service_info: BluetoothServiceInfoBleak) -> SensorUpdate:
        # BluetoothServiceInfoBleak is defined in HA, otherwise would just pass it
        # directly to the parser code
        # Make sure the device we have is one that we can connect with
        # in case its coming from a passive scanner
        if service_info.connectable:
            connectable_device = service_info.device
        elif device := async_ble_device_from_address(
            hass, service_info.device.address, True
        ):
            connectable_device = device
        else:
            # We have no bluetooth controller that is in range of
            # the device to poll it
            raise RuntimeError(
                f"No connectable device found for {service_info.device.address}"
            )
        try:
            return await data.async_poll(
                connectable_device,
                ble_device_callback=lambda: async_ble_device_from_address(
                    hass, service_info.device.address, True
                )
                or connectable_device,
            )
        finally:
            _clear_advertisement_history()

    coordinator = hass.data.setdefault(DOMAIN, {})[
        entry.entry_id
    ] = ActiveBluetoothProcessorCoordinator(
        hass,
        _LOGGER,
        address=address,
        mode=BluetoothScanningMode.PASSIVE,
        update_method=data.update,
        needs_poll_method=_needs_poll,
        poll_method=_async_poll,
        # We will take advertisements from non-connectable devices
        # since we will trade the BLEDevice for a connectable one
        # if we need to poll it
        connectable=False,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(
        # only start after all platforms have had a chance to subscribe
        coordinator.async_start()
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok