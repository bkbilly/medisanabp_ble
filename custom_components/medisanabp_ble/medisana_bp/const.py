"""Constants for MedisanaBP BLE parser"""

CHARACTERISTIC_BLOOD_PRESSURE = "00002a35-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_BATTERY = "00002a19-0000-1000-8000-00805f9b34fb"
# Bluetooth Current Time Service: read/write the device clock
CHARACTERISTIC_CURRENT_TIME = "00002a2b-0000-1000-8000-00805f9b34fb"
# Only set the device clock if it is off by more than this many seconds
CLOCK_TOLERANCE_S = 60
UPDATE_INTERVAL = 120
