# esphome (gałąź pomocnicza, niezwiązana z integracją gate_access)

Pakiety ESPHome dla boneIO ESP Cover Mix (boneio-cm-l-07), wspólna magistrala RS-485:

- `thessla/airpack4.yaml` – Thessla AirPack4 (Modbus adres 10) jako podurządzenie „Thessla AirPack4”.
- `deye_3p_lv/*.yaml` – Deye SG0XLP3 (3F LV) skopiowane z
  [Lewa-Reka/esphome-deye-inverter](https://github.com/Lewa-Reka/esphome-deye-inverter)
  (commit 501b4aa, Apache-2.0, patrz LICENSE/NOTICE). Jedyna zmiana: `device_id: ${deye_device_id}`
  przy każdej encji (podurządzenie w HA). Bez safe mode, grid_settings i time_sync.

Plik urządzenia w ESPHome definiuje: uart, `modbus1`, kontroler `deye_modbus`, `esphome.devices` (deye)
oraz substitutions `modbus_controller_id` i `deye_device_id`.
