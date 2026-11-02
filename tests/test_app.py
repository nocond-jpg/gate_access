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


# ---- device helpers (1.8) ---------------------------------------------------
import base64 as _b64
from cryptography.hazmat.primitives import hashes as _h, serialization as _ser
from cryptography.hazmat.primitives.asymmetric import ec as _ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature as _dds


class Phone:
    def __init__(self, client, base):
        self.client, self.base = client, base
        self.key = _ec.generate_private_key(_ec.SECP256R1())
        self.pub = _b64.b64encode(self.key.public_key().public_bytes(
            _ser.Encoding.DER, _ser.PublicFormat.SubjectPublicKeyInfo)).decode()
        self.dev = None

    async def register(self):
        r = await self.client.post(f"{self.base}?do=register", json={"pub": self.pub, "label": "iPhone · aplikacja"})
        j = await r.json()
        self.dev = j.get("dev")
        return j

    def sign(self, msg: bytes) -> str:
        r_, s_ = _dds(self.key.sign(msg, _ec.ECDSA(_h.SHA256())))
        return _b64.b64encode(r_.to_bytes(32, "big") + s_.to_bytes(32, "big")).decode()

    async def act(self, action, target, nonce=None, tamper=False):
        if nonce is None:
            c = await (await self.client.post(f"{self.base}?do=challenge", json={"dev": self.dev})).json()
            if not c.get("ok"):
                return 403, c, None
            nonce = c["nonce"]
        wid = self.base.rsplit("/", 1)[1]
        msg = "|".join(["v1", wid, self.dev, nonce, action, target if not tamper else "x"]).encode()
        r = await self.client.post(f"{self.base}?do={action}&t={target}",
                                   json={"dev": self.dev, "nonce": nonce, "sig": self.sign(msg)})
        return r.status, await r.json(), nonce


async def _approved_phone(hass, entry, client, base):
    from custom_components.gate_access import async_device_action
    ph = Phone(client, base)
    j = await ph.register()
    assert j["status"] == "pending"
    await async_device_action(hass, entry, ph.dev, "approve")
    return ph


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

    # unsigned app request is refused (link copied elsewhere / Shortcuts with ?t=)
    r = await client.post(f"{base}?do=open&t={WICKET}")
    assert r.status == 403
    # pending device: no state, no open
    ph = Phone(client, base)
    assert (await ph.register())["status"] == "pending"
    r = await client.post(f"{base}?do=state", json={"dev": ph.dev})
    assert r.status == 403
    st, js, _ = await ph.act("open", WICKET)
    assert st == 403
    assert calls == []
    # approve → works
    from custom_components.gate_access import async_device_action
    await async_device_action(hass, entry, ph.dev, "approve")
    r = await client.post(f"{base}?do=state", json={"dev": ph.dev})
    js = await r.json()
    assert {t["id"]: t["open"] for t in js["targets"]} == {GATE: False, WICKET: False}

    # SSRF guard: only known push services
    r = await client.post(f"{base}?do=subscribe", json={**APPLE, "endpoint": "https://192.168.0.1/x", "dev": ph.dev})
    assert r.status == 400
    r = await client.post(f"{base}?do=subscribe", json={**APPLE, "dev": ph.dev})
    assert (await r.json())["ok"]
    assert user["push_subs"][0]["dev"] == ph.dev

    # target outside the person's list is refused
    st, js, _ = await ph.act("open", "cover.inna")
    assert st == 403

    # open the wicket from the app (signed)
    st, js, nonce = await ph.act("open", WICKET)
    assert js["ok"], js
    await hass.async_block_till_done()
    assert ("lock", "unlock", WICKET) in calls
    # replay of the same nonce
    st, js, _ = await ph.act("open", WICKET, nonce=nonce)
    assert st == 403
    # tampered target in signed message
    st, js, _ = await ph.act("open", WICKET, tamper=True)
    assert st == 403
    # another phone with its own (unapproved) key cannot use the approved device id
    thief = Phone(client, base)
    thief.dev = ph.dev
    st, js, _ = await thief.act("open", WICKET)
    assert st == 403

    # close from the app
    st, js, _ = await ph.act("close", GATE)
    assert js["ok"]
    await hass.async_block_till_done()
    assert ("cover", "close_cover", GATE) in calls

    # removing the device drops its push subscription and access
    await async_device_action(hass, entry, ph.dev, "remove")
    assert user["push_subs"] == []
    r = await client.post(f"{base}?do=devstatus", json={"dev": ph.dev})
    assert (await r.json())["status"] == "unknown"
    ph2 = await _approved_phone(hass, entry, client, base)

    hist = hass.data[DOMAIN][entry.entry_id]["history"]
    assert any(h["source"] == "app" and h["status"] == "opened" for h in hist)

    # disabled person: app refuses
    user["enabled"] = False
    st, js, _ = await ph2.act("open", GATE)
    assert st == 403


async def test_reminder(hass: HomeAssistant, hass_client_no_auth):
    entry, calls = await _setup(hass, remind=10)
    client = await hass_client_no_auth()
    user = await async_create_user(hass, entry, "Ola", target=GATE)
    base = f"/api/webhook/{user['webhook_id']}"
    ph = await _approved_phone(hass, entry, client, base)
    await client.post(f"{base}?do=subscribe", json={**APPLE, "dev": ph.dev})

    sent = []

    def fake_send(sub, payload, priv, claim):
        sent.append(payload)
        return 201

    with patch("custom_components.gate_access.app._send_one", side_effect=fake_send):
        st, js, _ = await ph.act("open", GATE)
        assert js["ok"]
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
        await ph.act("open", GATE)
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



async def test_approval_via_notification(hass: HomeAssistant, hass_client_no_auth):
    entry, calls = await _setup(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.data, "approve_notify": "notify.mobile_app_samsung"})
    await hass.async_block_till_done()
    sent = []

    async def notify(call):
        sent.append(call.data)

    hass.services.async_register("notify", "mobile_app_samsung", notify)
    client = await hass_client_no_auth()
    user = await async_create_user(hass, entry, "Gość", target=GATE)
    base = f"/api/webhook/{user['webhook_id']}"
    ph = Phone(client, base)
    await ph.register()
    await hass.async_block_till_done()
    assert len(sent) == 1
    acts = [a["action"] for a in sent[0]["data"]["actions"]]
    assert acts[0].endswith(ph.dev)
    # re-registering the same key does not spam
    await ph.register()
    await hass.async_block_till_done()
    assert len(sent) == 1
    # tapping "Zatwierdź" on the Samsung
    hass.bus.async_fire("mobile_app_notification_action", {"action": acts[0]})
    await hass.async_block_till_done()
    st, js, _ = await ph.act("open", GATE)
    assert js["ok"]
    # plain single link still works without any device
    r = await client.get(base)
    assert r.status == 200
    # reject flow
    ph2 = Phone(client, base)
    await ph2.register()
    await hass.async_block_till_done()
    rej = [a["action"] for a in sent[-1]["data"]["actions"]][1]
    hass.bus.async_fire("mobile_app_notification_action", {"action": rej})
    await hass.async_block_till_done()
    r = await client.post(f"{base}?do=devstatus", json={"dev": ph2.dev})
    assert (await r.json())["status"] == "unknown"
