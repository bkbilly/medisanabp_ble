[![GitHub Release](https://img.shields.io/github/release/bkbilly/medisanabp_ble.svg?style=flat-square)](https://github.com/bkbilly/medisanabp_ble/releases)
[![License](https://img.shields.io/github/license/bkbilly/medisanabp_ble.svg?style=flat-square)](LICENSE)
[![hacs](https://img.shields.io/badge/HACS-default-orange.svg?style=flat-square)](https://hacs.xyz)


# Medisana Blood Pressure BLE
Integrates Bluetooth LE (https://www.medisana.com/en/Health-control/Blood-pressure-monitor/) to Home Assistant using active connection to get infromation from the sensors.

Exposes the following sensors:
 - Battery
 - Diastolic pressure
 - Systolic pressure
 - Pulses
 - Measured date

## Installation

Easiest install is via [HACS](https://hacs.xyz/):

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bkbilly&repository=medisanabp_ble&category=integration)

`HACS -> Explore & Add Repositories -> Medisana Blood Pressure BLE`

The device will be autodiscovered once the data are received by any bluetooth proxy.
