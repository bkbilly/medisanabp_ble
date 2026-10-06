from __future__ import annotations

import logging
import asyncio
import contextlib
from datetime import datetime
from typing import Callable

from bleak import BLEDevice, BleakClient, BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    establish_connection,
)
from bluetooth_data_tools import short_address
from bluetooth_sensor_state_data import BluetoothData
from home_assistant_bluetooth import BluetoothServiceInfo
from sensor_state_data import SensorDeviceClass, SensorUpdate, Units
from sensor_state_data.enum import StrEnum

from .const import (
    CHARACTERISTIC_BLOOD_PRESSURE,
    CHARACTERISTIC_BATTERY,
    CHARACTERISTIC_CURRENT_TIME,
    CLOCK_TOLERANCE_S,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# On connect some devices (e.g. BW 360 connect) replay ALL stored records, roughly one
# per second, grouped by user and oldest first. Keep receiving until the device has
# been quiet for QUIET_PERIOD_S (capped at MAX_COLLECT_S), then pick the newest record.
QUIET_PERIOD_S = 3.0
MAX_COLLECT_S = 180.0


def decode_sfloat(raw: int) -> float | None:
    """Decode IEEE 11073 16-bit SFLOAT to float."""
    mantissa = raw & 0x0FFF
    if mantissa >= 0x0800:
        mantissa -= 0x1000
    exponent = raw >> 12
    if exponent >= 0x08:
        exponent -= 0x10
    # Special values in IEEE 11073 (NaN, NRes, +/- Infinity)
    if mantissa in (0x07FE, 0x07FF, -0x0800, 0x0800, 0x0802):
        return None
    return mantissa * (10 ** exponent)


class MedisanaBPSensor(StrEnum):

    SYSTOLIC = "systolic"
    DIASTOLIC = "diastolic"
    PULSE = "pulse"
    SIGNAL_STRENGTH = "signal_strength"
    BATTERY_PERCENT = "battery_percent"
    TIMESTAMP = "timestamp"
    USER = "user"
    SYSTOLIC_2 = "systolic_2"
    DIASTOLIC_2 = "diastolic_2"
    PULSE_2 = "pulse_2"
    TIMESTAMP_2 = "timestamp_2"


class MedisanaBPBluetoothDeviceData(BluetoothData):
    """Data for MedisanaBP BLE sensors."""

    def __init__(self) -> None:
        super().__init__()
        self._event = asyncio.Event()
        self._has_data = False
        self._last_poll_successful = False
        self._records: list[dict] = []

    def _start_update(self, service_info: BluetoothServiceInfo) -> None:
        """Update from BLE advertisement data."""
        _LOGGER.debug("Parsing MedisanaBP BLE advertisement data: %s", service_info)
        self.set_device_manufacturer("Medisana")
        self.set_device_type("Blood Pressure Measurement")
        name = f"{service_info.name} {short_address(service_info.address)}"
        self.set_device_name(name)
        self.set_title(name)

    def poll_needed(
        self, service_info: BluetoothServiceInfo, last_poll: float | None
    ) -> bool:
        """
        This is called every time we get a service_info for a device. It means the
        device is working and online.
        """
        if not last_poll:
            return True
        if not self._last_poll_successful:
            return last_poll > 2
        return last_poll > UPDATE_INTERVAL

    def notification_handler(self, _, data: bytearray | bytes) -> None:
        """Helper for blood pressure measurement indications."""
        # All records of a poll are collected; the newest one is selected in
        # _apply_latest_record().
        _LOGGER.debug(
            "Raw indication %s: %s",
            len(self._records) + 1,
            bytes(data).hex() if data else "-",
        )
        if not data or len(data) < 7:
            _LOGGER.warning(
                "Received invalid blood pressure data length: %s",
                len(data) if data else 0,
            )
            return

        flags = data[0]
        # Bit 0: 0 = mmHg, 1 = kPa
        is_kpa = bool(flags & 0x01)
        # Bit 1: Time Stamp Flag
        has_timestamp = bool(flags & 0x02)
        # Bit 2: Pulse Rate Flag
        has_pulse = bool(flags & 0x04)
        # Bit 3: User ID Flag
        has_user_id = bool(flags & 0x08)
        # Bit 4: Measurement Status Flag
        has_status = bool(flags & 0x10)

        raw_syst = data[1] | (data[2] << 8)
        raw_diast = data[3] | (data[4] << 8)
        raw_arter = data[5] | (data[6] << 8)

        syst = decode_sfloat(raw_syst)
        diast = decode_sfloat(raw_diast)
        arter = decode_sfloat(raw_arter)

        if syst is None or diast is None:
            _LOGGER.warning("Invalid blood pressure values in data: %s", data.hex())
            return

        if is_kpa:
            # 1 kPa = 7.50062 mmHg
            syst = round(syst * 7.50062)
            diast = round(diast * 7.50062)
        else:
            syst = round(syst)
            diast = round(diast)

        offset = 7
        date = None
        if has_timestamp:
            if len(data) >= offset + 7:
                dyear = data[offset] | (data[offset + 1] << 8)
                dmonth = data[offset + 2]
                dday = data[offset + 3]
                dhour = data[offset + 4]
                dminu = data[offset + 5]
                dsec = data[offset + 6]
                offset += 7
                try:
                    if (
                        dyear >= 2000
                        and 1 <= dmonth <= 12
                        and 1 <= dday <= 31
                        and 0 <= dhour <= 23
                        and 0 <= dminu <= 59
                    ):
                        dsec = min(59, max(0, dsec))
                        date = datetime(
                            dyear, dmonth, dday, dhour, dminu, dsec
                        ).astimezone()
                except (ValueError, OverflowError) as err:
                    _LOGGER.debug(
                        "Could not parse date (%04d-%02d-%02d %02d:%02d:%02d): %s",
                        dyear,
                        dmonth,
                        dday,
                        dhour,
                        dminu,
                        dsec,
                        err,
                    )
            else:
                _LOGGER.warning(
                    "Packet flagged timestamp but data was truncated (len=%d)",
                    len(data),
                )

        puls = None
        if has_pulse:
            if len(data) >= offset + 2:
                raw_puls = data[offset] | (data[offset + 1] << 8)
                puls_val = decode_sfloat(raw_puls)
                if puls_val is not None:
                    puls = round(puls_val)
                offset += 2
            else:
                _LOGGER.warning(
                    "Packet flagged pulse rate but data was truncated (len=%d)",
                    len(data),
                )

        user = None
        if has_user_id:
            if len(data) >= offset + 1:
                user = data[offset] + 1
                offset += 1

        self._records.append(
            {"syst": syst, "diast": diast, "puls": puls, "user": user, "date": date}
        )
        _LOGGER.debug(
            "Record %s from device (syst: %s, diast: %s, puls: %s, user: %s, date: %s)",
            len(self._records),
            syst,
            diast,
            puls,
            user,
            date,
        )
        self._has_data = True
        self._event.set()

    @staticmethod
    def _select_latest(records: list[dict]) -> dict:
        """Return the newest record.

        The record with the latest measurement timestamp wins; without any decodable
        timestamp the last received record wins. This works for devices that send the
        oldest record first as well as for devices that send the newest first.
        """
        dated = [
            (r["date"], index, r)
            for index, r in enumerate(records)
            if r["date"] is not None
        ]
        if dated:
            return max(dated, key=lambda item: (item[0], item[1]))[2]
        return records[-1]

    def _apply_latest_record(self) -> None:
        """Write the newest collected record of each user to the sensors.

        User 2 gets its own sensors with the suffix "_2". All other records (user 1,
        no user id, unknown user) keep the existing sensors, so their entity ids do
        not change.
        """
        if not self._records:
            return
        _LOGGER.debug("%s records received", len(self._records))
        for user_id, suffix, name_suffix in ((1, "", ""), (2, "_2", " User 2")):
            records = [
                r for r in self._records if (r["user"] == 2) == (user_id == 2)
            ]
            if not records:
                continue
            record = self._select_latest(records)
            _LOGGER.info(
                "User %s: %s records received, using syst: %s, diast: %s, puls: %s, "
                "date: %s",
                user_id,
                len(records),
                record["syst"],
                record["diast"],
                record["puls"],
                record["date"],
            )
            if record["date"] is not None:
                self.update_sensor(
                    key=str(MedisanaBPSensor.TIMESTAMP) + suffix,
                    native_unit_of_measurement=None,
                    native_value=record["date"],
                    name="Measured Date" + name_suffix,
                )
            if user_id == 1 and record["user"] is not None:
                self.update_sensor(
                    key=str(MedisanaBPSensor.USER),
                    native_unit_of_measurement=None,
                    native_value=record["user"],
                    name="User",
                )
            self.update_sensor(
                key=str(MedisanaBPSensor.SYSTOLIC) + suffix,
                native_unit_of_measurement=Units.PRESSURE_MMHG,
                native_value=record["syst"],
                device_class=SensorDeviceClass.PRESSURE,
                name="Systolic" + name_suffix,
            )
            self.update_sensor(
                key=str(MedisanaBPSensor.DIASTOLIC) + suffix,
                native_unit_of_measurement=Units.PRESSURE_MMHG,
                native_value=record["diast"],
                device_class=SensorDeviceClass.PRESSURE,
                name="Diastolic" + name_suffix,
            )
            if record["puls"] is not None:
                self.update_sensor(
                    key=str(MedisanaBPSensor.PULSE) + suffix,
                    native_unit_of_measurement="bpm",
                    native_value=record["puls"],
                    name="Pulse" + name_suffix,
                )

    async def _sync_clock(self, client: BleakClient) -> None:
        """Set the device clock to local time if it is off.

        The measurement timestamps come from the device clock, which is often
        wrong (e.g. after a battery change). Read the Current Time characteristic
        (0x2A2B) and write the local time if the difference exceeds
        CLOCK_TOLERANCE_S. Errors are only logged; reading the measurements must
        never fail because of this.
        """
        try:
            if client.services.get_characteristic(CHARACTERISTIC_CURRENT_TIME) is None:
                return
            raw = bytes(await client.read_gatt_char(CHARACTERISTIC_CURRENT_TIME))
            now = datetime.now().astimezone()
            device_time = None
            if len(raw) >= 7:
                try:
                    device_time = datetime(
                        raw[0] | (raw[1] << 8), raw[2], raw[3], raw[4], raw[5], raw[6]
                    ).replace(tzinfo=now.tzinfo)
                except ValueError:
                    device_time = None
            diff = abs((now - device_time).total_seconds()) if device_time else None
            _LOGGER.debug(
                "Device clock: %s (raw %s), difference: %s s",
                device_time,
                raw.hex(),
                None if diff is None else round(diff),
            )
            if diff is not None and diff <= CLOCK_TOLERANCE_S:
                return
            # Current Time: Exact Time 256 + Adjust Reason (0x01 = manual time update)
            new_time = bytes(
                [
                    now.year & 0xFF,
                    now.year >> 8,
                    now.month,
                    now.day,
                    now.hour,
                    now.minute,
                    now.second,
                    now.isoweekday(),
                    0,
                    0x01,
                ]
            )
            await client.write_gatt_char(
                CHARACTERISTIC_CURRENT_TIME, new_time, response=True
            )
            _LOGGER.info(
                "Device clock was %s, set to %s",
                device_time,
                now.strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception as err:
            _LOGGER.debug("Could not set device clock: %s", err)

    async def _collect_remaining_records(self, client: BleakClient) -> None:
        """Receive records until the device has been quiet for QUIET_PERIOD_S."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + MAX_COLLECT_S
        while client.is_connected and loop.time() < deadline:
            count = len(self._records)
            # Set again by the next record or by a disconnect.
            self._event.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._event.wait(), QUIET_PERIOD_S)
            if len(self._records) == count:
                break

    async def _read_battery(self, client: BleakClient, address: str) -> None:
        """Read the battery level if the device is still connected."""
        if not client.is_connected:
            return
        try:
            if battery_char := client.services.get_characteristic(
                CHARACTERISTIC_BATTERY
            ):
                battery_payload = await client.read_gatt_char(battery_char)
                if battery_payload:
                    self.update_sensor(
                        key=str(MedisanaBPSensor.BATTERY_PERCENT),
                        native_unit_of_measurement=Units.PERCENTAGE,
                        native_value=battery_payload[0],
                        device_class=SensorDeviceClass.BATTERY,
                        name="Battery",
                    )
        except (BleakError, asyncio.TimeoutError) as err:
            _LOGGER.debug("Could not read battery from %s: %s", address, err)

    async def async_poll(
        self,
        ble_device: BLEDevice,
        ble_device_callback: Callable[[], BLEDevice] | None = None,
    ) -> SensorUpdate:
        """Poll the device to retrieve any values we can't get from passive listening."""
        _LOGGER.debug("Connecting to BLE device: %s", ble_device.address)
        self._event.clear()
        self._has_data = False
        self._records = []
        self._last_poll_successful = False
        subscribed = False

        def _disconnected_callback(client: BleakClient) -> None:
            _LOGGER.debug("BLE device disconnected: %s", client.address)
            self._event.set()

        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            ble_device.address,
            disconnected_callback=_disconnected_callback,
            ble_device_callback=ble_device_callback,
        )
        try:
            try:
                await client.start_notify(
                    CHARACTERISTIC_BLOOD_PRESSURE, self.notification_handler
                )
            except Exception as err:
                _LOGGER.warning(
                    "Bleak error starting notify on %s: %s", ble_device.address, err
                )
                return self._finish_update()
            subscribed = True

            # Sync the clock right away: some devices disconnect on their own
            # shortly after they have sent their last stored record.
            await self._sync_clock(client)

            # Wait for the first stored measurement to arrive.
            try:
                await asyncio.wait_for(self._event.wait(), 15)
            except asyncio.TimeoutError:
                # The device answered but has no record it has not sent before.
                _LOGGER.debug("No new records from %s", ble_device.address)
            except Exception as err:
                _LOGGER.warning(
                    "Error waiting for data from %s: %s", ble_device.address, err
                )

            # Read the battery now: some devices disconnect on their own shortly
            # after they have sent their last stored record.
            await self._read_battery(client, ble_device.address)

            if self._has_data:
                try:
                    await self._collect_remaining_records(client)
                except Exception as err:
                    _LOGGER.warning(
                        "Error collecting records from %s: %s", ble_device.address, err
                    )
        finally:
            if client.is_connected:
                try:
                    await client.stop_notify(CHARACTERISTIC_BLOOD_PRESSURE)
                except (BleakError, asyncio.TimeoutError, Exception) as err:
                    _LOGGER.debug(
                        "Error stopping notify on %s: %s", ble_device.address, err
                    )
                try:
                    await client.disconnect()
                except (BleakError, asyncio.TimeoutError, Exception) as err:
                    _LOGGER.debug(
                        "Error disconnecting %s: %s", ble_device.address, err
                    )
            _LOGGER.debug(
                "Disconnected from active bluetooth client: %s", ble_device.address
            )

        self._apply_latest_record()
        # A poll counts as successful once the device answered, even if it had no
        # new record; otherwise every following advertisement would poll again.
        self._last_poll_successful = subscribed
        return self._finish_update()
