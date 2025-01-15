[![GitHub Release](https://img.shields.io/github/release/syphernl/silvercrestbp_ble.svg?style=flat-square)](https://github.com/syphernl/silvercrestbp_ble/releases)
[![License](https://img.shields.io/github/license/syphernl/silvercrestbp_ble.svg?style=flat-square)](LICENSE)
[![hacs](https://img.shields.io/badge/HACS-default-orange.svg?style=flat-square)](https://hacs.xyz)


# Silvercrest Blood Pressure BLE
Integrates Silvercrest Blood Pressure mesurement to Home Assistant using active connection to get infromation from the sensors. Based on (https://github.com/bkbilly/medisanabp_ble)[medisanabp_ble] by (https://github.com/bkbilly)[@bkbilly]

Exposes the following sensors:
 - Diastolic pressure
 - Systolic pressure
 - Pulses
 - Measured date

## Installation

Easiest install is via [HACS](https://hacs.xyz/):

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=syphernl&repository=silvercrestbp_ble&category=integration)

`HACS -> Explore & Add Repositories -> Silvercrest Blood Pressure BLE`

The device will be autodiscovered once the data are received by any bluetooth proxy.
