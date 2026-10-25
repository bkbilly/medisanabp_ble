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

# One event per stored record of a poll. The sensors only show the newest record of
# each user; an older one (left on the device after a failed poll) would otherwise be
# lost. Automations can store those records from this event.
EVENT_RECORD = f"{DOMAIN}_record"

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
            update = await data.async_poll(
                connectable_device,
                ble_device_callback=lambda: async_ble_device_from_address(
                    hass, service_info.device.address, True
                )
                or connectable_device,
            )
        finally:
            _clear_advertisement_history()
        _fire_record_events(hass, address, data.last_records)
        return update

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


def _fire_record_events(hass: HomeAssistant, address: str, records: list[dict]) -> None:
    """Fire EVENT_RECORD for every record of the last poll, per user, newest flagged."""
    for user_id in (1, 2):
        own = [r for r in records if (r["user"] == 2) == (user_id == 2)]
        if not own:
            continue
        newest = MedisanaBPBluetoothDeviceData._select_latest(own)
        for record in own:
            hass.bus.async_fire(
                EVENT_RECORD,
                {
                    "address": address,
                    "user": user_id,
                    "systolic": record["syst"],
                    "diastolic": record["diast"],
                    "pulse": record["puls"],
                    "measured": record["date"].isoformat() if record["date"] else None,
                    "newest": record is newest,
                    "newest_measured": newest["date"].isoformat()
                    if newest["date"]
                    else None,
                },
            )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok