"""Tests for Gate Access 2.0: Bluetooth key sync to ESPHome (brama_ble)."""

import base64
from datetime import timedelta
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.gate_access import async_create_user, async_update_user, async_device_action
from custom_components.gate_access import ble as gble

from .test_app import GATE, WICKET, Phone, _approved_phone, _setup  # noqa: F401  (auto_enable)
from .test_app import auto_enable  # noqa: F401


async def _ble_setup(hass):
    entry, calls = await _setup(hass)
    sent = []

    async def esp(call):
        sent.append(call.data["klucze"])

    hass.services.async_register("esphome", "brama_ble_klucze", esp)
    hass.config_entries.async_update_entry(
        entry, options={**entry.data, "ble_map": {GATE: "esphome.brama_ble_klucze", WICKET: ""}}
    )
    await hass.async_block_till_done()
    return hass.config_entries.async_get_entry(entry.entry_id), sent


def _parse(payload):
    out = {}
    for item in filter(None, payload.split(";")):
        dev, pt, frm, until = item.split(",")
        out[dev] = (base64.b64decode(pt), int(frm), int(until))
    return out


async def _flush(hass):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=40))
    await hass.async_block_till_done()


async def test_ble_sync_and_signature(hass: HomeAssistant, hass_client_no_auth):
    entry, sent = await _ble_setup(hass)
    client = await hass_client_no_auth()
    owner = await async_create_user(hass, entry, "Dawid", target=GATE)
    base = f"/api/webhook/{owner['webhook_id']}"
    ph = await _approved_phone(hass, entry, client, base)
    pending = Phone(client, base)
    await pending.register()                       # not approved -> no key
    guest = await async_create_user(
        hass, entry, "Gość", target=GATE,
        expires_at=(dt_util.utcnow() + timedelta(days=1)).isoformat(),
    )
    gph = await _approved_phone(hass, entry, client, f"/api/webhook/{guest['webhook_id']}")
    counted = await async_create_user(hass, entry, "Kurier", target=GATE, uses_total=1)
    await _approved_phone(hass, entry, client, f"/api/webhook/{counted['webhook_id']}")
    wicket = await async_create_user(hass, entry, "Furtka", target=WICKET)
    await _approved_phone(hass, entry, client, f"/api/webhook/{wicket['webhook_id']}")
    await _flush(hass)

    assert sent, "keys were not pushed"
    keys = _parse(sent[-1])
    assert set(keys) == {ph.dev, gph.dev}           # approved, unlimited count, this target
    assert list(keys)[0] == ph.dev                  # unlimited first
    pt, frm, until = keys[ph.dev]
    assert len(pt) == 65 and pt[0] == 4 and frm == 0 and until == 0
    assert keys[gph.dev][2] > 0                     # time-limited guest carries its expiry

    # ESP-side check: the phone's ECDSA over "ble1|dev|nonce|open" verifies with the pushed point
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pt)
    raw = base64.b64decode(ph.sign(b"ble1|" + ph.dev.encode() + b"|00ff|open"))
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub.verify(der, b"ble1|" + ph.dev.encode() + b"|00ff|open", ec.ECDSA(hashes.SHA256()))
    assert gble.version_of(sent[-1]) == gble.version_of(sent[-1])

    # unchanged -> no resend; disabling a person -> new list without their key
    n = len(sent)
    await _flush(hass)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=400))
    await hass.async_block_till_done()
    assert len(sent) == n
    await async_update_user(hass, entry, guest["webhook_id"], enabled=False)
    await _flush(hass)
    assert set(_parse(sent[-1])) == {ph.dev}
    # removing the device -> empty list
    await async_device_action(hass, entry, ph.dev, "reject")
    await _flush(hass)
    assert sent[-1] == ""

    # forced resend service
    n = len(sent)
    await hass.services.async_call("gate_access", "sync_ble_keys", {}, blocking=True)
    assert len(sent) == n + 1


async def test_ble_buttons_in_app(hass: HomeAssistant, hass_client_no_auth):
    entry, _ = await _ble_setup(hass)
    client = await hass_client_no_auth()
    user = await async_create_user(hass, entry, "Marek", target=GATE)
    user["app_targets"] = [GATE, WICKET]
    base = f"/api/webhook/{user['webhook_id']}"
    html = await (await client.get(f"{base}?app=1")).text()
    assert html.count("data-ble='open'") == 1       # only the gate has BLE configured
    assert "d38d0001-7e51-45b5-9f87-ae96680dcaf6" in html
    sw = await (await client.get(f"{base}?asset=sw")).text()
    assert "caches.open" in sw
    # use-limited link: no BLE (count cannot be enforced offline)
    one = await async_create_user(hass, entry, "Raz", target=GATE, uses_total=1)
    html = await (await client.get(f"/api/webhook/{one['webhook_id']}?app=1")).text()
    assert "data-ble=" not in html


def test_service_name():
    assert gble.service_name("esphome.brama_ble_klucze") == "brama_ble_klucze"
    assert gble.service_name("brama ble") is None
    assert gble.service_name("") is None
