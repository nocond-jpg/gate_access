"""Tests for Gate Access 1.7: mobile app, Web Push, reminders."""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from datetime import timedelta

from custom_components.gate_access import async_create_user
from custom_components.gate_access.const import DOMAIN

GATE = "cover.brama"
WICKET = "lock.furtka"
CONTACT = "binary_sensor.brama_kontaktron"
APPLE = {"endpoint": "https://web.push.apple.com/QGx", "keys": {"p256dh": "BPk" + "a" * 84, "auth": "x" * 22}}


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations):
    yield


async def _setup(hass: HomeAssistant, remind=10):
    calls = []

    async def svc(call):
        calls.append((call.domain, call.service, call.data["entity_id"]))

    hass.states.async_set(GATE, "closed", {"friendly_name": "Brama wjazdowa"})
    hass.states.async_set(WICKET, "locked", {"friendly_name": "Furtka"})
    hass.states.async_set(CONTACT, "off")
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "webhook", {})
    entry = MockConfigEntry(
        domain=DOMAIN, title="Gate Access",
        data={"targets": [GATE, WICKET], "log_path": "/tmp/gate_test.log",
              "state_map": {GATE: CONTACT}, "remind_map": {GATE: remind, WICKET: 0}},
    )
    entry.add_to_hass(hass)
    hass.config.components.update({"frontend", "panel_custom"})
    with patch("custom_components.gate_access._register_panel"), \
         patch("custom_components.gate_access.add_extra_js_url"):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for d, sv in (("cover", "open_cover"), ("cover", "close_cover"), ("lock", "unlock"), ("lock", "lock")):
        hass.services.async_register(d, sv, svc)
    return entry, calls


async def test_app_flow(hass: HomeAssistant, hass_client_no_auth):
    entry, calls = await _setup(hass)
    client = await hass_client_no_auth()
    user = await async_create_user(hass, entry, "Marek", target=GATE)
    user["app_targets"] = [GATE, WICKET]
    base = f"/api/webhook/{user['webhook_id']}"

    # app page does NOT open anything
    r = await client.get(f"{base}?app=1")
    assert r.status == 200
    html = await r.text()
    assert "Brama wjazdowa" in html and "Furtka" in html and "manifest" in html
    assert calls == []

    for a, ctype in (("manifest", "manifest"), ("sw", "javascript"), ("icon-180", "png")):
        r = await client.get(f"{base}?asset={a}")
        assert r.status == 200 and ctype in r.headers["Content-Type"]

    r = await client.get(f"{base}?do=state")
    js = await r.json()
    assert {t["id"]: t["open"] for t in js["targets"]} == {GATE: False, WICKET: False}

    # SSRF guard: only known push services
    r = await client.post(f"{base}?do=subscribe", json={**APPLE, "endpoint": "https://192.168.0.1/x"})
    assert r.status == 400
    r = await client.post(f"{base}?do=subscribe", json=APPLE, headers={"User-Agent": "iPhone"})
    assert (await r.json())["ok"]
    assert user["push_subs"][0]["label"] == "iPhone"

    # target outside the person's list is refused
    r = await client.post(f"{base}?do=open&t=cover.inna")
    assert r.status == 403

    # open the wicket from the app
    r = await client.post(f"{base}?do=open&t={WICKET}")
    assert (await r.json())["ok"]
    await hass.async_block_till_done()
    assert ("lock", "unlock", WICKET) in calls

    # close from the app
    r = await client.post(f"{base}?do=close&t={GATE}")
    assert (await r.json())["ok"]
    await hass.async_block_till_done()
    assert ("cover", "close_cover", GATE) in calls

    hist = hass.data[DOMAIN][entry.entry_id]["history"]
    assert any(h["source"] == "app" and h["status"] == "opened" for h in hist)

    # disabled person: app refuses
    user["enabled"] = False
    r = await client.post(f"{base}?do=open&t={GATE}")
    assert r.status == 403


async def test_reminder(hass: HomeAssistant, hass_client_no_auth):
    entry, calls = await _setup(hass, remind=10)
    client = await hass_client_no_auth()
    user = await async_create_user(hass, entry, "Ola", target=GATE)
    base = f"/api/webhook/{user['webhook_id']}"
    await client.post(f"{base}?do=subscribe", json=APPLE)

    sent = []

    def fake_send(sub, payload, priv, claim):
        sent.append(payload)
        return 201

    with patch("custom_components.gate_access.app._send_one", side_effect=fake_send):
        r = await client.post(f"{base}?do=open&t={GATE}")
        assert (await r.json())["ok"]
        hass.states.async_set(CONTACT, "on")  # gate physically open
        await hass.async_block_till_done()

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=10, seconds=5))
        await hass.async_block_till_done()
        assert len(sent) == 1 and "nadal otwarta" in sent[0]

        # contact reports closed → reminder cancelled
        hass.states.async_set(CONTACT, "off")
        await hass.async_block_till_done()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=25))
        await hass.async_block_till_done()
        assert len(sent) == 1

        # opened again but closed before 10 min → no reminder
        await client.post(f"{base}?do=open&t={GATE}")
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=40))
        await hass.async_block_till_done()
        assert len(sent) == 1


async def test_dead_subscription_removed(hass: HomeAssistant):
    entry, _ = await _setup(hass)
    user = await async_create_user(hass, entry, "Ewa", target=GATE)
    user["push_subs"] = [dict(APPLE, label="iPhone")]
    from custom_components.gate_access import app as gapp

    with patch("custom_components.gate_access.app._send_one", return_value=410):
        n = await gapp.send_push(hass, entry, user, "t", "b", "/x", "t")
    assert n == 0 and user["push_subs"] == []


def test_vapid_and_real_encryption():
    """Real pywebpush encryption with our VAPID key format (no network)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    import base64
    from custom_components.gate_access import app as gapp

    v = gapp.new_vapid()
    client_key = ec.generate_private_key(ec.SECP256R1())
    n = client_key.public_key().public_numbers()
    p256 = base64.urlsafe_b64encode(b"\x04" + n.x.to_bytes(32, "big") + n.y.to_bytes(32, "big")).rstrip(b"=").decode()
    auth = base64.urlsafe_b64encode(b"0123456789abcdef").rstrip(b"=").decode()
    sub = {"endpoint": "https://web.push.apple.com/abc", "keys": {"p256dh": p256, "auth": auth}}

    captured = {}

    class Resp:
        status_code = 201
        text = ""

    def fake_post(url, data=None, headers=None, timeout=None):
        captured.update(url=url, headers=headers, data=data)
        return Resp()

    with patch("requests.post", side_effect=fake_post):
        status = gapp._send_one(sub, '{"title":"x"}', v["private"], "https://gate.example.pl")
    assert status == 201
    assert captured["headers"]["content-encoding"] == "aes128gcm"
    assert captured["headers"]["authorization"].startswith("vapid t=")
    assert len(captured["data"]) > 20


async def test_legacy_link_and_options(hass: HomeAssistant, hass_client_no_auth):
    entry, calls = await _setup(hass)
    client = await hass_client_no_auth()
    user = await async_create_user(hass, entry, "Stary", target=GATE)
    # 1.6 behaviour unchanged: visiting the link opens and returns HTML
    r = await client.get(f"/api/webhook/{user['webhook_id']}")
    assert r.status == 200 and "text/html" in r.headers["Content-Type"]
    await hass.async_block_till_done()
    assert ("cover", "open_cover", GATE) in calls

    # options: reminders step keeps other settings
    hass.config_entries.async_update_entry(entry, options={**entry.data, "rate_limit": 5})
    await hass.async_block_till_done()
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "reminders"})
    assert flow["step_id"] == "reminders"
    res = await hass.config_entries.options.async_configure(
        flow["flow_id"], {f"state__{GATE}": CONTACT, f"remind__{GATE}": 15, f"remind__{WICKET}": 0}
    )
    assert res["data"]["remind_map"] == {GATE: 15, WICKET: 0}
    assert res["data"]["state_map"] == {GATE: CONTACT}
    assert res["data"]["rate_limit"] == 5


async def test_shortcuts_and_solo(hass: HomeAssistant, hass_client_no_auth):
    import json as _json

    entry, calls = await _setup(hass)
    client = await hass_client_no_auth()
    user = await async_create_user(hass, entry, "Kuba", target=GATE)
    user["app_targets"] = [GATE, WICKET]
    base = f"/api/webhook/{user['webhook_id']}"

    m = _json.loads(await (await client.get(f"{base}?asset=manifest")).text())
    assert [s["url"] for s in m["shortcuts"]] == [f"{base}?app=1&quick={GATE}", f"{base}?app=1&quick={WICKET}"]

    ms = _json.loads(await (await client.get(f"{base}?asset=manifest&solo={WICKET}")).text())
    assert ms["name"] == "Furtka" and ms["start_url"] == f"{base}?app=1&solo={WICKET}"

    r = await client.get(f"{base}?app=1&solo={WICKET}")
    html = await r.text()
    assert r.status == 200 and "<title>Furtka</title>" in html and "Brama wjazdowa" not in html
    # visiting quick/solo pages never opens server-side
    await client.get(f"{base}?app=1&quick={GATE}")
    await hass.async_block_till_done()
    assert calls == []

    r = await client.get(f"{base}?app=1&solo=cover.obca")
    assert r.status == 403
    for k in ("garage-180", "garage-512"):
        assert (await client.get(f"{base}?asset={k}")).status == 200
