# esphome-deye (gałąź pomocnicza, niezwiązana z gate_access)

Pakiety ESPHome dla Deye SG0XLP3 (3F LV) skopiowane z
[Lewa-Reka/esphome-deye-inverter](https://github.com/Lewa-Reka/esphome-deye-inverter)
(commit 501b4aa, Apache-2.0, patrz LICENSE/NOTICE).

Jedyna zmiana: każda encja ma `device_id: ${deye_device_id}`, żeby w Home Assistant
falownik był osobnym podurządzeniem ESPHome (na boneIO razem z AirPack4).

Wymagane substitutions: `modbus_controller_id`, `deye_device_id`.
Nie zawiera: safe mode, grid_settings, time_sync.
