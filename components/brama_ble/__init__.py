"""brama_ble – awaryjne otwieranie bramy przez Bluetooth LE (Gate Access).

Komponent tylko dołącza nagłówek brama_ble.h (weryfikacja poleceń ECDSA P-256).
Konfiguracja usługi GATT i akcji API jest w YAML urządzenia (brama.yaml).
"""

import esphome.codegen as cg
import esphome.config_validation as cv

CODEOWNERS = ["@nocond-jpg"]
DEPENDENCIES = ["esp32_ble_server"]

CONFIG_SCHEMA = cv.Schema({})


async def to_code(config):
    cg.add_global(cg.RawStatement('#include "esphome/components/brama_ble/brama_ble.h"'))
