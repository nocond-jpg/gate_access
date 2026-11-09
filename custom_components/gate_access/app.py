"""Mobile web app (PWA), Web Push and open-gate reminders for Gate Access.

Everything is served from the person's own webhook URL, so nothing new has to be
exposed to the internet:

    /api/webhook/<id>?app=1          app page (home-screen web app)
    /api/webhook/<id>?asset=...      manifest / service worker / icons
    /api/webhook/<id>?do=state       JSON state of the person's openings
    /api/webhook/<id>?do=open&t=..   open a specific opening (POST)
    /api/webhook/<id>?do=close&t=..  close a specific opening (POST)
    /api/webhook/<id>?do=subscribe   register a Web Push subscription (POST)

Push goes out from HA to the browser vendor's push service (Apple / Google /
Mozilla) with VAPID — no Apple Developer account and no HA app needed.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

from cryptography.hazmat.primitives.asymmetric import ec

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_BASE_URL,
    CONF_REMIND_MAP,
    CONF_STATE_MAP,
    DEFAULT_REMIND_MIN,
    DOMAIN,
    PUSH_HOSTS,
    PUSH_MAX_PER_USER,
    REMIND_REPEATS,
)

_LOGGER = logging.getLogger(__name__)

_FRONTEND = os.path.join(os.path.dirname(__file__), "frontend")
ICONS = {
    "icon-180": "icon-180.png", "icon-192": "icon-192.png", "icon-512": "icon-512.png",
    "garage-180": "garage-180.png", "garage-192": "garage-192.png", "garage-512": "garage-512.png",
}


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _cfg(entry: ConfigEntry, key: str, default=None):
    return entry.options.get(key, entry.data.get(key, default))


# ---------------------------------------------------------------------------
# VAPID keys (generated once, kept in the integration's storage)
# ---------------------------------------------------------------------------
def new_vapid() -> dict[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_numbers().private_value.to_bytes(32, "big")
    nums = key.public_key().public_numbers()
    pub = b"\x04" + nums.x.to_bytes(32, "big") + nums.y.to_bytes(32, "big")
    return {"private": _b64u(priv), "public": _b64u(pub)}


# ---------------------------------------------------------------------------
# State of an opening (open / closed / unknown)
# ---------------------------------------------------------------------------
def state_entity(entry: ConfigEntry, target: str) -> str:
    smap = _cfg(entry, CONF_STATE_MAP, {}) or {}
    return smap.get(target) or target


def is_open(hass: HomeAssistant, entry: ConfigEntry, target: str) -> bool | None:
    """True = open, False = closed, None = unknown / not determinable."""
    eid = state_entity(entry, target)
    st = hass.states.get(eid)
    if st is None or st.state in ("unknown", "unavailable", ""):
        return None
    domain = eid.split(".", 1)[0]
    s = st.state
    if domain == "binary_sensor":
        return s == "on"
    if domain == "cover":
        if s in ("open", "opening"):
            return True
        if s in ("closed", "closing"):
            return False
        return None
    if domain == "lock":
        if s in ("unlocked", "open", "opening", "unlocking"):
            return True
        if s in ("locked", "locking"):
            return False
        return None
    if domain in ("switch", "input_boolean", "light"):
        return s == "on"
    return None  # button / script without a state sensor


def remind_minutes(hass: HomeAssistant, entry: ConfigEntry, target: str) -> int:
    rmap = _cfg(entry, CONF_REMIND_MAP, {}) or {}
    try:
        minutes = int(rmap.get(target, DEFAULT_REMIND_MIN))
    except (TypeError, ValueError):
        minutes = 0
    return max(0, minutes)


# ---------------------------------------------------------------------------
# Push subscriptions
# ---------------------------------------------------------------------------
def valid_subscription(sub: Any) -> dict | None:
    """Accept only well-formed subscriptions to known push services (no SSRF)."""
    if not isinstance(sub, dict):
        return None
    endpoint = sub.get("endpoint")
    keys = sub.get("keys") or {}
    if not isinstance(endpoint, str) or len(endpoint) > 1024:
        return None
    url = urlparse(endpoint)
    host = (url.hostname or "").lower()
    if url.scheme != "https" or not any(host == h or host.endswith("." + h) for h in PUSH_HOSTS):
        return None
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (isinstance(p256dh, str) and isinstance(auth, str)) or len(p256dh) > 200 or len(auth) > 64:
        return None
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


def add_subscription(user: dict, sub: dict, label: str) -> None:
    subs = [s for s in user.get("push_subs", []) if s["endpoint"] != sub["endpoint"]]
    subs.append({**sub, "label": label[:60]})
    user["push_subs"] = subs[-PUSH_MAX_PER_USER:]


def remove_subscription(user: dict, endpoint: str) -> bool:
    subs = user.get("push_subs", [])
    kept = [s for s in subs if s["endpoint"] != endpoint]
    user["push_subs"] = kept
    return len(kept) != len(subs)


def _vapid_sub(hass: HomeAssistant, entry: ConfigEntry) -> str:
    base = (_cfg(entry, CONF_BASE_URL, "") or "").rstrip("/")
    if not base.startswith("https://"):
        base = (hass.config.external_url or "").rstrip("/")
    return base if base.startswith("https://") else "mailto:gate-access@example.com"


def _send_one(sub: dict, payload: str, private_key: str, sub_claim: str) -> int:
    """Blocking send (executor). Returns HTTP status (0 = network error)."""
    from pywebpush import WebPushException, webpush  # imported lazily

    try:
        resp = webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
            data=payload,
            vapid_private_key=private_key,
            vapid_claims={"sub": sub_claim},
            ttl=900,
            headers={"Urgency": "high"},
            timeout=10,
        )
        return getattr(resp, "status_code", 201)
    except WebPushException as err:
        return err.response.status_code if err.response is not None else 0
    except Exception:  # noqa: BLE001
        return 0


async def send_push(
    hass: HomeAssistant, entry: ConfigEntry, user: dict, title: str, body: str,
    url: str, tag: str,
) -> int:
    """Send to all of a person's devices. Drops dead subscriptions. Returns #delivered."""
    data = hass.data[DOMAIN][entry.entry_id]
    vapid = data["vapid"]
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag})
    sub_claim = _vapid_sub(hass, entry)
    ok = 0
    dead: list[str] = []
    for sub in list(user.get("push_subs", [])):
        status = await hass.async_add_executor_job(
            _send_one, sub, payload, vapid["private"], sub_claim
        )
        if 200 <= status < 300:
            ok += 1
        elif status in (404, 410):
            dead.append(sub["endpoint"])
        else:
            _LOGGER.warning("Gate Access push to %s failed: HTTP %s", user["name"], status)
    for ep in dead:
        remove_subscription(user, ep)
    return ok


# ---------------------------------------------------------------------------
# Reminders: "opening still open after N minutes" → push to whoever opened it
# ---------------------------------------------------------------------------
@callback
def cancel_reminder(hass: HomeAssistant, entry: ConfigEntry, target: str) -> None:
    rem = hass.data[DOMAIN][entry.entry_id].setdefault("reminders", {})
    item = rem.pop(target, None)
    if item and item.get("cancel"):
        item["cancel"]()


@callback
def cancel_all(hass: HomeAssistant, entry: ConfigEntry) -> None:
    rem = hass.data[DOMAIN][entry.entry_id].get("reminders", {})
    for target in list(rem):
        cancel_reminder(hass, entry, target)


@callback
def schedule_reminder(
    hass: HomeAssistant, entry: ConfigEntry, target: str, webhook_id: str,
    app_url: str, target_name: str,
) -> None:
    """(Re)start the reminder for a target after a person opened it."""
    minutes = remind_minutes(hass, entry, target)
    cancel_reminder(hass, entry, target)
    if minutes <= 0:
        return
    data = hass.data[DOMAIN][entry.entry_id]
    user = next((u for u in data["users"] if u["webhook_id"] == webhook_id), None)
    if not user or not user.get("push_subs"):
        return
    rem = data.setdefault("reminders", {})
    state = {"count": 0, "cancel": None}

    async def _fire(_now) -> None:
        if rem.get(target) is not state:
            return
        opened = is_open(hass, entry, target)
        if opened is not True:  # closed or unknown → stop
            rem.pop(target, None)
            return
        u = next((x for x in data["users"] if x["webhook_id"] == webhook_id), None)
        if not u or not u.get("enabled", True):
            rem.pop(target, None)
            return
        state["count"] += 1
        total = minutes * state["count"]
        await send_push(
            hass, entry, u,
            f"{target_name} – nadal otwarta",
            f"Otwarta od {total} min. Dotknij, aby zamknąć.",
            f"{app_url}&focus={target}", f"remind-{target}",
        )
        await data["save"]()
        if state["count"] < REMIND_REPEATS:
            state["cancel"] = async_call_later(hass, minutes * 60, _fire)
        else:
            rem.pop(target, None)

    state["cancel"] = async_call_later(hass, minutes * 60, _fire)
    rem[target] = state


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------
def read_icon(name: str) -> bytes | None:
    fname = ICONS.get(name)
    if not fname:
        return None
    with open(os.path.join(_FRONTEND, fname), "rb") as handle:
        return handle.read()


def _icons(path: str, kind: str = "icon") -> list[dict]:
    return [
        {"src": f"{path}?asset={kind}-192", "sizes": "192x192", "type": "image/png"},
        {"src": f"{path}?asset={kind}-512", "sizes": "512x512", "type": "image/png"},
    ]


def manifest(path: str, person: str, targets: list[dict], solo: dict | None = None) -> str:
    """Main app manifest (with long-press shortcuts) or a one-opening quick icon."""
    base = {
        "scope": path,
        "display": "standalone",
        "background_color": "#14171c",
        "theme_color": "#14171c",
    }
    if solo:
        return json.dumps(
            {
                **base,
                "name": solo["name"],
                "short_name": solo["name"][:12],
                "id": f"{path}?solo={solo['id']}",
                "start_url": f"{path}?app=1&solo={solo['id']}",
                "icons": _icons(path, solo["kind"]),
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            **base,
            "name": f"Brama – {person}",
            "short_name": "Brama",
            "id": path,
            "start_url": f"{path}?app=1",
            "icons": _icons(path),
            # Android: long-press the app icon → "Otwórz: …" (can be dragged to the home screen).
            # Chrome re-reads this manifest on app launch and rebuilds the shortcut list when it changes.
            "shortcuts": [
                {
                    "name": f"Otwórz: {t['name']}",
                    "short_name": t["name"][:12],
                    "url": f"{path}?app=1&quick={t['id']}",
                    "icons": [{"src": f"{path}?asset={t['kind']}-192", "sizes": "192x192", "type": "image/png"}],
                }
                for t in targets[:4]
            ],
        },
        ensure_ascii=False,
    )


SERVICE_WORKER = """
// 2.0: the app page and icons are cached so the app opens without network / with HA down
// (then only Bluetooth control works). Network first; cache only on failure or 5xx.
const CACHE = 'gate-app-v2';
self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET') return;
  const u = new URL(r.url);
  const asset = u.searchParams.get('asset');
  const page = r.mode === 'navigate' && u.searchParams.get('app') === '1';
  if (!page && !(asset && asset !== 'sw')) return;
  e.respondWith((async () => {
    const c = await caches.open(CACHE);
    const fallback = async () => (await c.match(r)) || (page ? await c.match(u.origin + u.pathname + '?app=1') : undefined);
    try {
      const res = await fetch(r, {cache: 'no-store'});
      if (res.ok && (res.headers.get('content-type') || '').match(/html|png|json|svg|image/)) { c.put(r, res.clone()); return res; }
      if (res.status >= 500) { const hit = await fallback(); if (hit) return hit; }
      return res;
    } catch (err) {
      const hit = await fallback(); if (hit) return hit;
      throw err;
    }
  })());
});
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = {title: 'Brama', body: e.data ? e.data.text() : ''}; }
  e.waitUntil(self.registration.showNotification(d.title || 'Brama', {
    body: d.body || '', tag: d.tag || 'gate', data: {url: d.url || self.registration.scope + '?app=1'},
    icon: self.registration.scope + '?asset=icon-192', badge: self.registration.scope + '?asset=icon-192'
  }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || (self.registration.scope + '?app=1');
  e.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(list => {
    for (const c of list) { if ('focus' in c) { c.navigate(url); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});
"""


# ---------------------------------------------------------------------------
# App page
# ---------------------------------------------------------------------------
_APP_CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;min-height:100vh;font-family:-apple-system,system-ui,sans-serif;background:#14171c;color:#e8e6df;
 padding:calc(env(safe-area-inset-top) + 18px) 16px calc(env(safe-area-inset-bottom) + 24px)}
h1{font-size:1.5rem;font-weight:650;margin:0 0 2px}
.sub{color:#8b9099;font-size:.9rem;margin-bottom:18px}
.card{background:#1e2229;border:1px solid #2a2f37;border-radius:8px;padding:16px;margin-bottom:12px}
.card.focus{border-color:#c8952f}
.row{display:flex;align-items:center;gap:10px}
.nm{font-size:1.15rem;font-weight:600;flex:1}
.st{font-size:.82rem;padding:3px 9px;border-radius:999px;background:#2a2f37;color:#a9aeb6}
.st.open{background:#5a3d10;color:#ffcf7a}.st.closed{background:#1f3a2a;color:#8fd6a8}
.btns{display:flex;gap:10px;margin-top:14px}
button{flex:1;border-radius:8px;padding:16px 10px;font-size:1.05rem;font-weight:650;border:1px solid #c8952f;cursor:pointer}
button.op{background:#c8952f;color:#1a1206}
button.cl{background:transparent;color:#e8b45a}
button:disabled{opacity:.55}
.msg{min-height:1.3em;margin-top:10px;font-size:.88rem;color:#a9aeb6}
.msg.err{color:#ff8a80}.msg.ok{color:#8fd6a8}
.push{margin-top:22px;font-size:.9rem;color:#a9aeb6}
.push button{width:100%;margin-top:8px;background:transparent;color:#e8b45a;padding:12px;font-size:.95rem}
.hint{background:#1e2229;border:1px dashed #3a4049;border-radius:8px;padding:12px;margin-top:10px;line-height:1.45}
.hint.err{border-color:#c9524a}.hint.wait{border-color:#c8952f}
.hint button{display:block;width:100%;margin-top:10px;background:transparent;color:#e8b45a;padding:12px;font-size:.95rem}
#dev .hint{margin:0 0 12px}
body.car{padding:24px 28px}body.car h1{font-size:2rem}body.car #list{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
body.car .card{margin:0;padding:22px}body.car .nm{font-size:1.5rem}body.car .st{font-size:1rem}
body.car button.op,body.car button.cl{padding:30px 10px;font-size:1.5rem}
.carcode{font-size:2.6rem;letter-spacing:.3em;text-align:center;font-weight:700;color:#ffcf7a;margin:10px 0}
button.carpair{margin-top:18px;background:transparent;color:#e8b45a;width:100%}
.pin{display:block;width:100%;margin-top:10px;background:transparent;border:0;color:#8b9099;font-size:.82rem;
 font-weight:500;padding:6px;text-decoration:underline}
body.solo .card{padding:22px}body.solo button.op{padding:34px 10px;font-size:1.4rem}
.quick{font-size:1.05rem;text-align:center;margin:6px 0 14px;color:#e8b45a}
.bt{display:flex;gap:10px;margin-top:10px}.bt[hidden]{display:none}
.bt button{background:transparent;color:#8fb8ff;border-color:#3b5f99;padding:12px 8px;font-size:.95rem}
body.offline .btns{display:none}body.offline .bt button{padding:22px 8px;font-size:1.15rem}
"""

_APP_JS = r"""
const Q = new URLSearchParams(location.search);
const P = location.pathname, WID = P.split('/').pop(), VAPID = __VAPID__;
const FOCUS = Q.get('focus'), SOLO = Q.get('solo'), QUICK = Q.get('quick');
const CAR = Q.get('car') === '1' || /Tesla/.test(navigator.userAgent);
const $ = s => document.querySelector(s);
const standalone = window.navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;
const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
let busy = {}, DEV = null, STATUS = 'unknown', OFFLINE = false;
const HAS_BT = !!(navigator.bluetooth && navigator.bluetooth.requestDevice);

function label(v){ return v === true ? ['Otwarta','open'] : v === false ? ['Zamknięta','closed'] : ['—','']; }
async function api(q, opt){
  opt = Object.assign({cache:'no-store'}, opt || {});
  if (opt.json !== undefined) { opt.method = opt.method || 'POST'; opt.headers = {'Content-Type':'application/json'}; opt.body = JSON.stringify(opt.json); delete opt.json; }
  const r = await fetch(P + q, opt); let j = {}; try { j = await r.json(); } catch(e) {} j._status = r.status; return j; }
function b64(buf){ return btoa(String.fromCharCode(...new Uint8Array(buf))); }
function b64ToBytes(s){ s = s.replace(/-/g,'+').replace(/_/g,'/'); s += '='.repeat((4 - s.length % 4) % 4);
  const r = atob(s), o = new Uint8Array(r.length); for (let i=0;i<r.length;i++) o[i] = r.charCodeAt(i); return o; }

// ---- device key: non-extractable P-256 key kept in IndexedDB ------------------
function idb(){ return new Promise((res, rej) => { const r = indexedDB.open('gate-access', 1);
  r.onupgradeneeded = () => r.result.createObjectStore('keys'); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); }); }
async function idbGet(k){ const db = await idb(); return new Promise((res, rej) => {
  const q = db.transaction('keys').objectStore('keys').get(k); q.onsuccess = () => res(q.result); q.onerror = () => rej(q.error); }); }
async function idbPut(k, v){ const db = await idb(); return new Promise((res, rej) => {
  const t = db.transaction('keys', 'readwrite'); t.objectStore('keys').put(v, k); t.oncomplete = () => res(); t.onerror = () => rej(t.error); }); }
function devLabel(){ const ua = navigator.userAgent;
  if (CAR) return 'Tesla · przeglądarka';
  const d = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android' : /Mac/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows' : 'urządzenie';
  const name = SOLO ? (document.title || '') : '';
  return d + (standalone ? ' · aplikacja' : ' · przeglądarka') + (name ? ' · ' + name : ''); }

async function loadDevice(){
  let rec = await idbGet(P);
  if (!rec) {
    const kp = await crypto.subtle.generateKey({name:'ECDSA', namedCurve:'P-256'}, false, ['sign','verify']);
    rec = {key: kp.privateKey, pub: b64(await crypto.subtle.exportKey('spki', kp.publicKey)), dev: null};
    await idbPut(P, rec);
  }
  return rec;
}
async function register(rec){
  const j = await api('?do=register', {json: {pub: rec.pub, label: devLabel()}});
  if (j.ok) { rec.dev = j.dev; await idbPut(P, rec); }
  return j;
}
async function checkDevice(){
  if (!window.crypto || !crypto.subtle || !window.indexedDB) { banner('err', 'Ta przeglądarka nie obsługuje bezpiecznych kluczy.'); return; }
  DEV = await loadDevice();
  let j;
  try { j = DEV.dev ? await api('?do=devstatus', {json: {dev: DEV.dev}}) : await register(DEV); }
  catch (e) { j = {_status: 0}; }
  if (!j._status || j._status >= 500) { goOffline(); return; }
  if (OFFLINE) goOnline();
  if (!DEV.dev && !j.ok) { banner('err', j.msg || 'Nie udało się zarejestrować urządzenia.'); return; }
  if (j.status === 'unknown') {
    if (DEV.dev) { banner('err', 'To urządzenie zostało usunięte z listy dozwolonych.', true); setStatus('removed'); return; }
    j = await register(DEV);
    if (!j.ok) { banner('err', j.msg || 'Nie udało się zarejestrować urządzenia.'); return; }
  }
  setStatus(j.status);
}
function setStatus(st){
  const was = STATUS; STATUS = st;
  document.querySelectorAll('[data-act]').forEach(b => b.disabled = st !== 'approved');
  showBt(st === 'approved');
  if (st === 'approved') { banner(); if (was !== 'approved') onApproved(); }
  else if (st === 'pending') { banner('wait', 'To urządzenie czeka na zatwierdzenie przez administratora. Strona odświeży się sama.'); setTimeout(pollStatus, 5000); }
}
async function pollStatus(){
  if (STATUS !== 'pending' || !DEV || !DEV.dev) return;
  const j = await api('?do=devstatus', {json: {dev: DEV.dev}}).catch(() => ({}));
  if (j.status === 'unknown') { banner('err', 'Prośba o dostęp została odrzucona.', true); STATUS = 'removed'; return; }
  setStatus(j.status || 'pending');
}
function banner(kind, text, retry){
  const b = $('#dev'); if (!kind) { b.innerHTML = ''; return; }
  b.innerHTML = '<div class="hint ' + kind + '">' + text + (retry ? '<button id="rereg">Poproś ponownie o dostęp</button>' : '') + '</div>';
  if (retry) $('#rereg').onclick = async () => { const j = await register(DEV); if (j.ok) setStatus(j.status); else banner('err', j.msg || 'Nie udało się.', true); };
}
async function signed(action, target){
  const c = await api('?do=challenge', {json: {dev: DEV.dev}});
  if (!c.ok) return c;
  const msg = ['v1', WID, DEV.dev, c.nonce, action, target].join('|');
  const sig = await crypto.subtle.sign({name:'ECDSA', hash:'SHA-256'}, DEV.key, new TextEncoder().encode(msg));
  return api('?do=' + action + '&t=' + encodeURIComponent(target), {json: {dev: DEV.dev, nonce: c.nonce, sig: b64(sig)}});
}

// ---- Bluetooth (2.0): emergency control straight to the ESP, no network/HA needed --------
// The ESP issues a one-time nonce; the phone signs "ble1|dev|nonce|action" with its device key.
const BLE_S = 'd38d0001-7e51-45b5-9f87-ae96680dcaf6', BLE_C = n => 'd38d000' + n + '-7e51-45b5-9f87-ae96680dcaf6';
const BLE_MSG = {OK: 'Wykonano', NONCE: 'Nie zdążyło – spróbuj jeszcze raz',
  NIEZNANE: 'Brama nie zna tego telefonu. Gdy będziesz w zasięgu domu, otwórz raz aplikację – klucze wgrają się same.',
  ZLY_PODPIS: 'Odrzucono (zły podpis)', WYGASLO: 'Twój dostęp wygasł', POZA_OKNEM: 'Twój dostęp jeszcze się nie zaczął',
  BRAK_CZASU: 'Brama nie zna aktualnej godziny – dostęp czasowy nie działa teraz przez Bluetooth',
  BLOKADA: 'Za dużo błędnych prób – odczekaj 10 minut', FORMAT: 'Błąd transmisji – spróbuj jeszcze raz',
  WYLACZONE: 'Otwieranie przez Bluetooth jest wyłączone w sterowniku'};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const bytes = v => new Uint8Array(v.buffer, v.byteOffset, v.byteLength);
function showBt(on){ document.querySelectorAll('.bt').forEach(r => r.hidden = !(HAS_BT && on)); }
function goOffline(){
  OFFLINE = true; document.body.classList.add('offline');
  if (DEV && DEV.dev && HAS_BT && document.querySelector('.bt')) {
    banner('wait', 'Brak połączenia z domem. Możesz sterować przez <b>Bluetooth</b> – podejdź do bramy.'); showBt(true);
  } else if (DEV && DEV.dev && document.querySelector('.bt')) {
    banner('err', 'Brak połączenia z domem. Bluetooth wymaga Chrome na Androidzie.');
  } else banner('err', 'Brak połączenia z domem.');
  clearTimeout(window._retry); window._retry = setTimeout(() => checkDevice().catch(() => {}), 15000);
}
function goOnline(){ OFFLINE = false; document.body.classList.remove('offline'); banner(); }
async function bleTry(svc, action){
  const cN = await svc.getCharacteristic(BLE_C(2)), cP = await svc.getCharacteristic(BLE_C(3)), cR = await svc.getCharacteristic(BLE_C(4));
  let nonce = null;
  for (let i = 0; i < 8 && !nonce; i++) { await sleep(250); const v = bytes(await cN.readValue()); if (v.length === 16 && v.some(b => b)) nonce = v; }
  if (!nonce) throw new Error('Brama nie odpowiada');
  const hex = Array.from(nonce, b => b.toString(16).padStart(2, '0')).join('');
  const sig = new Uint8Array(await crypto.subtle.sign({name: 'ECDSA', hash: 'SHA-256'}, DEV.key,
    new TextEncoder().encode('ble1|' + DEV.dev + '|' + hex + '|' + action)));
  const cmd = new Uint8Array(83); cmd[0] = 1; cmd[1] = action === 'open' ? 1 : 2; cmd[2] = 16;
  cmd.set(new TextEncoder().encode(DEV.dev), 3); cmd.set(sig, 19);
  const total = Math.ceil(cmd.length / 18);
  for (let i = 0; i < total; i++) {
    const part = cmd.slice(i * 18, (i + 1) * 18), pkt = new Uint8Array(part.length + 2);
    pkt[0] = i; pkt[1] = total; pkt.set(part, 2);
    await (cP.writeValueWithResponse ? cP.writeValueWithResponse(pkt) : cP.writeValue(pkt));
  }
  let res = '';
  for (let i = 0; i < 10; i++) { await sleep(200); res = new TextDecoder().decode(bytes(await cR.readValue())); if (res && res !== 'GOTOWY') break; }
  return res;
}
async function bleAct(id, action){
  if (busy[id]) return; busy[id] = true;
  const card = document.getElementById('c-' + id), msg = card.querySelector('.msg');
  card.querySelectorAll('button').forEach(b => b.disabled = true);
  msg.className = 'msg'; msg.textContent = 'Wybierz bramę z listy Bluetooth…';
  let gatt = null;
  try {
    if (!DEV || !DEV.dev || !DEV.key) throw new Error('Ten telefon nie jest jeszcze zatwierdzony');
    const d = await navigator.bluetooth.requestDevice({filters: [{services: [BLE_S]}]});
    msg.textContent = 'Łączę przez Bluetooth…';
    gatt = await d.gatt.connect();
    const svc = await gatt.getPrimaryService(BLE_S);
    let res = await bleTry(svc, action);
    if (res === 'NONCE' || res === 'FORMAT') res = await bleTry(svc, action);
    if (res === 'OK') { msg.className = 'msg ok'; msg.textContent = (action === 'open' ? 'Otwarto' : 'Zamknięto') + ' przez Bluetooth';
      if (navigator.vibrate) navigator.vibrate(40); }
    else { msg.className = 'msg err'; msg.textContent = BLE_MSG[res] || ('Brama odpowiedziała: ' + (res || 'brak odpowiedzi')); }
  } catch (e) {
    msg.className = 'msg err';
    msg.textContent = e.name === 'NotFoundError' ? 'Anulowano wybór urządzenia'
      : e.name === 'NetworkError' ? 'Nie udało się połączyć – podejdź bliżej bramy i spróbuj ponownie'
      : e.name === 'SecurityError' ? 'Bluetooth zablokowany przez przeglądarkę'
      : (e.message || 'Błąd Bluetooth');
  } finally {
    try { if (gatt && gatt.connected) gatt.disconnect(); } catch (e) {}
    setTimeout(() => { busy[id] = false; card.querySelectorAll('button').forEach(b => b.disabled = b.dataset.ble ? false : STATUS !== 'approved'); }, 1500);
  }
}

// ---- UI ---------------------------------------------------------------------
async function refresh(){
  if (STATUS !== 'approved') return;
  try {
    const j = await api('?do=state', {json: {dev: DEV.dev}});
    if (!j.ok) { if (j.status) setStatus(j.status); return; }
    for (const t of j.targets) {
      const card = document.getElementById('c-' + t.id); if (!card) continue;
      const [txt, cls] = label(t.open); const st = card.querySelector('.st');
      st.textContent = txt; st.className = 'st ' + cls;
    }
  } catch (e) {}
}

async function act(id, action){
  if (busy[id] || STATUS !== 'approved') return; busy[id] = true;
  const card = document.getElementById('c-' + id), msg = card.querySelector('.msg');
  card.querySelectorAll('button').forEach(b => b.disabled = true);
  msg.className = 'msg'; msg.textContent = action === 'open' ? 'Otwieram…' : 'Zamykam…';
  try {
    const j = await signed(action, id);
    if (j._status >= 500) throw new Error('offline');
    if (j.ok) { msg.className = 'msg ok'; msg.textContent = action === 'open' ? 'Otwarto' : 'Zamknięto';
      if (navigator.vibrate) navigator.vibrate(40); }
    else { msg.className = 'msg err'; msg.textContent = j.msg || j.title || 'Nie udało się';
      if (j.status && j.status !== 'approved') setStatus(j.status); }
  } catch (e) { msg.className = 'msg err'; msg.textContent = 'Błąd połączenia' + (HAS_BT && card.querySelector('.bt') ? ' – użyj Bluetooth' : '');
    showBt(true); }
  setTimeout(() => { busy[id] = false; card.querySelectorAll('button').forEach(b => b.disabled = STATUS !== 'approved'); }, 1500);
  setTimeout(refresh, 800); setTimeout(refresh, 4000);
}

async function pushUi(){
  const box = $('#push');
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    box.innerHTML = ios && !standalone
      ? '<div class="hint">Przypomnienia o otwartej bramie: w Safari dotknij <b>Udostępnij</b> → <b>Do ekranu początkowego</b>, potem otwórz Bramę z ikony.</div>'
      : '<div class="hint">Ta przeglądarka nie obsługuje powiadomień push.</div>';
    return;
  }
  const reg = await navigator.serviceWorker.register(P + '?asset=sw', {scope: P});
  await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (sub && Notification.permission === 'granted') {
    box.innerHTML = 'Przypomnienia o otwartej bramie: <b>włączone</b><button id="poff">Wyłącz przypomnienia</button>';
    $('#poff').onclick = async () => { await api('?do=unsubscribe', {json: {dev: DEV.dev, endpoint: sub.endpoint}}); await sub.unsubscribe(); pushUi(); };
    api('?do=subscribe', {json: Object.assign({dev: DEV.dev}, sub.toJSON())});
  } else {
    box.innerHTML = 'Przypomnienia o otwartej bramie: <b>wyłączone</b><button id="pon">Włącz przypomnienia</button>';
    $('#pon').onclick = async () => {
      try {
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') { box.insertAdjacentHTML('beforeend','<div class="hint">Brak zgody na powiadomienia (Ustawienia → Powiadomienia → Brama).</div>'); return; }
        const s = await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: b64ToBytes(VAPID)});
        const j = await api('?do=subscribe', {json: Object.assign({dev: DEV.dev}, s.toJSON())});
        if (!j.ok) throw new Error(j.msg || 'odrzucono');
        pushUi();
      } catch (e) { box.insertAdjacentHTML('beforeend','<div class="hint">Nie udało się: ' + e.message + '</div>'); }
    };
  }
}

document.querySelectorAll('[data-act]').forEach(b => { b.disabled = true; b.addEventListener('click', () => act(b.dataset.id, b.dataset.act)); });
document.querySelectorAll('[data-ble]').forEach(b => b.addEventListener('click', () => bleAct(b.dataset.id, b.dataset.ble)));
// Offline start needs the service worker (it serves the cached app page)
if ('serviceWorker' in navigator && !CAR) navigator.serviceWorker.register(P + '?asset=sw', {scope: P}).catch(() => {});

// "Ikona na ekran": a separate home-screen icon that opens one opening with a single tap
document.querySelectorAll('[data-pin]').forEach(b => b.addEventListener('click', async () => {
  const url = location.origin + P + '?app=1&solo=' + encodeURIComponent(b.dataset.pin);
  const card = document.getElementById('c-' + b.dataset.pin);
  let box = card.querySelector('.hint'); if (!box) { box = document.createElement('div'); box.className = 'hint'; card.appendChild(box); }
  try { await navigator.clipboard.writeText(url); } catch (e) {}
  box.innerHTML = ios
    ? 'Skopiowano link. Otwórz <b>Safari</b>, wklej link, potem <b>Udostępnij → Do ekranu początkowego</b>. Dotknięcie tej ikony od razu otwiera <b>' + b.dataset.name + '</b>. Ikona będzie wymagać jednorazowego zatwierdzenia.'
    : 'Skopiowano link. Otwórz go w <b>Chrome</b> → menu ⋮ → <b>Dodaj do ekranu głównego</b>. ' +
      'Szybciej: przytrzymaj ikonę Brama na ekranie i przeciągnij skrót „Otwórz: ' + b.dataset.name + '” na ekran.';
}));

// Car pairing: an approved phone issues a one-time code typed into the car browser
const cp = document.getElementById('carpair');
if (cp) cp.addEventListener('click', async () => {
  const box = $('#carbox'); if (STATUS !== 'approved') { box.innerHTML = '<div class="hint err">Najpierw to urządzenie musi być zatwierdzone.</div>'; return; }
  cp.disabled = true; box.innerHTML = '<div class="hint">Generuję kod…</div>';
  try {
    const j = await signed('carcode', 'car');
    if (!j.ok) { box.innerHTML = '<div class="hint err">' + (j.msg || 'Nie udało się') + '</div>'; return; }
    box.innerHTML = '<div class="hint">W samochodzie otwórz przeglądarkę i wpisz adres:<br><b>' + j.url + '</b><br>Następnie wpisz kod (ważny ' + Math.round(j.ttl / 60) + ' min, jednorazowy):<div class="carcode">' + j.code.slice(0,3) + ' ' + j.code.slice(3) + '</div>Po połączeniu dodaj stronę do ulubionych (gwiazdka).</div>';
  } catch (e) { box.innerHTML = '<div class="hint err">Błąd połączenia</div>'; }
  finally { setTimeout(() => { cp.disabled = false; }, 3000); }
});

const launchTarget = standalone ? (SOLO || QUICK) : null;
if (QUICK && !document.getElementById('c-' + QUICK)) {
  history.replaceState(null, '', P + '?app=1');
  $('#list').insertAdjacentHTML('afterbegin', '<div class="hint err" style="margin-bottom:12px">' +
    'Ten skrót jest nieaktualny – nie masz już dostępu do tego obiektu. ' + (ios ? '' :
    'Lista skrótów odświeży się sama po kolejnym uruchomieniu aplikacji (Android robi to zwykle raz na dobę). ' +
    'Od razu: odinstaluj aplikację Brama i zainstaluj ponownie z linku.') + '</div>');
} else if (SOLO && !standalone && !CAR) {
  $('#list').insertAdjacentHTML('afterbegin', '<div class="hint" style="margin-bottom:12px">' + (ios
    ? 'Dodaj tę stronę: <b>Udostępnij → Do ekranu początkowego</b>. Dotknięcie ikony od razu otworzy bramę.'
    : 'Dodaj tę stronę: menu ⋮ → <b>Dodaj do ekranu głównego</b>. Dotknięcie ikony od razu otworzy bramę.') + '</div>');
}
if (QUICK) history.replaceState(null, '', P + '?app=1');   // a reload must not open again

function onApproved(){
  refresh();
  // One-tap launch: only when started from a home-screen icon / app shortcut (never from a plain link)
  if (launchTarget && document.getElementById('c-' + launchTarget) && !window._launched) { window._launched = true; act(launchTarget, 'open'); }
  if (!SOLO && !CAR) pushUi().catch(() => {});
}
if (FOCUS) { const c = document.getElementById('c-' + FOCUS); if (c) { c.classList.add('focus'); c.scrollIntoView(); } }
setInterval(() => { if (!document.hidden) refresh(); }, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) { refresh(); pollStatus(); } });
checkDevice().catch(e => banner('err', 'Błąd: ' + e.message));
"""


def app_page(
    path: str, person: str, targets: list[dict], vapid_public: str, solo: dict | None = None,
    car: bool = False,
) -> str:
    cards = []
    for t in ([solo] if solo else targets):
        tid = html.escape(t["id"], quote=True)
        close_btn = (
            f"<button class='cl' data-act='close' data-id='{tid}'>Zamknij</button>"
            if t["can_close"] else ""
        )
        cards.append(
            f"<div class='card' id='c-{tid}'><div class='row'>"
            f"<span class='nm'>{html.escape(t['name'])}</span><span class='st'>—</span></div>"
            f"<div class='btns'><button class='op' data-act='open' data-id='{tid}'>Otwórz</button>"
            f"{close_btn}</div>"
            + (
                f"<div class='bt' hidden><button data-ble='open' data-id='{tid}'>"
                "Otwórz przez Bluetooth</button>"
                + (f"<button data-ble='close' data-id='{tid}'>Zamknij (Bluetooth)</button>"
                   if t["can_close"] else "")
                + "</div>"
                if t.get("ble") and not car else ""
            )
            + "<div class='msg'></div>"
            + ("" if (solo or car) else
               f"<button class='pin' data-pin='{tid}' data-name='{html.escape(t['name'], quote=True)}'>"
               "Ikona na ekran – otwieranie jednym dotknięciem</button>")
            + "</div>"
        )
    js = _APP_JS.replace("__VAPID__", json.dumps(vapid_public))
    p = html.escape(path, quote=True)
    title = html.escape(solo["name"] if solo else "Brama")
    mf = f"{p}?asset=manifest" + (f"&amp;solo={html.escape(solo['id'], quote=True)}" if solo else "")
    icon = f"{solo['kind']}-180" if solo else "icon-180"
    return (
        "<!doctype html><html lang='pl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
        "<meta name='apple-mobile-web-app-capable' content='yes'>"
        "<meta name='mobile-web-app-capable' content='yes'>"
        "<meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'>"
        f"<meta name='apple-mobile-web-app-title' content='{title}'>"
        "<meta name='theme-color' content='#14171c'>"
        "<meta name='referrer' content='no-referrer'>"
        "<meta name='robots' content='noindex,nofollow'>"
        f"<link rel='manifest' href='{mf}'>"
        f"<link rel='apple-touch-icon' href='{p}?asset={icon}'>"
        f"<link rel='icon' type='image/png' href='{p}?asset=icon-192'>"
        f"<title>{title}</title><style>{_APP_CSS}</style></head>"
        f"<body class='{'solo' if solo else ('car' if car else '')}'>"
        f"<h1>{title}</h1><div class='sub'>{html.escape(person)}</div>"
        "<div id='dev'></div>"
        f"<div id='list'>{''.join(cards) or '<div class=card>Brak przypisanych obiektów.</div>'}</div>"
        + ("" if (solo or car) else
           "<button class='carpair' id='carpair'>Połącz z samochodem (Tesla)</button><div id='carbox'></div>")
        + "<div class='push' id='push'></div>"
        f"<script>{js}</script></body></html>"
    )


# ---------------------------------------------------------------------------
# Device binding (1.8): the app signs every action with a non-extractable
# WebCrypto P-256 key; new devices must be approved by the administrator.
# ---------------------------------------------------------------------------
import secrets as _secrets
import time as _time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from .const import DEVICES_MAX_PER_USER, DEVICES_PENDING_MAX, NONCE_TTL, PENDING_TTL_DAYS


def _load_pub(b64: str):
    raw = base64.b64decode(b64, validate=True)
    if len(raw) > 200:
        raise ValueError("key too long")
    key = serialization.load_der_public_key(raw)
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("not P-256")
    return key


def device_public(dev: dict) -> dict:
    return {k: dev.get(k) for k in ("id", "label", "status", "created", "last_used")}


def find_device(user: dict, dev_id: Any) -> dict | None:
    if not isinstance(dev_id, str):
        return None
    return next((d for d in user.get("devices", []) if d["id"] == dev_id), None)


def _purge_pending(user: dict) -> None:
    limit = _time.time() - PENDING_TTL_DAYS * 86400
    user["devices"] = [
        d for d in user.get("devices", [])
        if d["status"] != "pending" or d.get("created_ts", 0) > limit
    ]


def register_device(user: dict, pub: Any, label: Any) -> tuple[dict | None, str]:
    """Create (or return the existing) device record. Returns (device, error)."""
    if not isinstance(pub, str):
        return None, "Brak klucza urządzenia"
    try:
        _load_pub(pub)
    except (ValueError, TypeError):
        return None, "Nieprawidłowy klucz urządzenia"
    _purge_pending(user)
    devices = user.setdefault("devices", [])
    existing = next((d for d in devices if d["pub"] == pub), None)
    if existing:
        return existing, ""
    if sum(1 for d in devices if d["status"] == "pending") >= DEVICES_PENDING_MAX:
        return None, "Za dużo oczekujących próśb o dostęp – poproś administratora"
    if len(devices) >= DEVICES_MAX_PER_USER:
        return None, "Osiągnięto limit urządzeń dla tej osoby"
    now = _time.time()
    dev = {
        "id": _secrets.token_hex(8),
        "pub": pub,
        "label": (label if isinstance(label, str) else "urządzenie")[:60],
        "status": "pending",
        "created": datetime_iso(now),
        "created_ts": now,
        "last_used": None,
    }
    devices.append(dev)
    return dev, ""


def datetime_iso(ts: float) -> str:
    from homeassistant.util import dt as dt_util

    return dt_util.utc_from_timestamp(ts).isoformat(timespec="seconds")


def issue_nonce(store: dict, dev_id: str) -> str | None:
    now = _time.monotonic()
    for k in [k for k, v in store.items() if v[1] < now]:
        store.pop(k)
    if sum(1 for v in store.values() if v[0] == dev_id) >= 5:
        return None
    nonce = _secrets.token_urlsafe(18)
    store[nonce] = (dev_id, now + NONCE_TTL)
    return nonce


def verify_action(
    store: dict, user: dict, webhook_id: str, body: Any, action: str, target: str
) -> tuple[dict | None, str]:
    """Check device approval, single-use nonce and ECDSA signature."""
    if not isinstance(body, dict):
        return None, "Wymagane zatwierdzone urządzenie"
    dev = find_device(user, body.get("dev"))
    if dev is None:
        return None, "Wymagane zatwierdzone urządzenie"
    if dev["status"] != "approved":
        return None, "Urządzenie czeka na zatwierdzenie"
    nonce, sig = body.get("nonce"), body.get("sig")
    rec = store.pop(nonce, None) if isinstance(nonce, str) else None
    if not rec or rec[0] != dev["id"] or rec[1] < _time.monotonic():
        return None, "Żądanie wygasło – spróbuj ponownie"
    if not isinstance(sig, str) or len(sig) > 200:
        return None, "Nieprawidłowy podpis"
    msg = "|".join(["v1", webhook_id, dev["id"], nonce, action, target]).encode()
    try:
        raw = base64.b64decode(sig, validate=True)
        if len(raw) != 64:
            raise ValueError
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        _load_pub(dev["pub"]).verify(der, msg, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, TypeError):
        return None, "Nieprawidłowy podpis"
    dev["last_used"] = datetime_iso(_time.time())
    return dev, ""


# ---------------------------------------------------------------------------
# Car pairing (1.9): car browsers (Tesla) cannot paste links. An approved phone
# asks for a one-time 6-digit code; the car opens the short address
# /api/webhook/tesla, types the code and gets its own device key, approved
# automatically because the code was issued by an already approved device.
# ---------------------------------------------------------------------------
from .const import CAR_CODE_FAIL_WINDOW, CAR_CODE_MAX_FAIL, CAR_CODE_TTL


def purge_car_codes(store: dict) -> None:
    now = _time.monotonic()
    for k in [k for k, v in store.items() if v["exp"] < now]:
        store.pop(k)


def issue_car_code(store: dict, webhook_id: str) -> str | None:
    now = _time.monotonic()
    purge_car_codes(store)
    if sum(1 for v in store.values() if v["wid"] == webhook_id) >= 3:
        return None
    while True:
        code = f"{_secrets.randbelow(1_000_000):06d}"
        if code not in store:
            break
    store[code] = {"wid": webhook_id, "exp": now + CAR_CODE_TTL}
    return code


def car_locked(fails: list) -> bool:
    now = _time.monotonic()
    fails[:] = [t for t in fails if t > now - CAR_CODE_FAIL_WINDOW]
    return len(fails) >= CAR_CODE_MAX_FAIL


def redeem_car_code(store: dict, fails: list, code: Any) -> str | None:
    """Return the person's webhook id for a valid code (single use)."""
    now = _time.monotonic()
    if car_locked(fails):
        return None
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    rec = store.pop(code, None) if len(code) == 6 else None
    if not rec or rec["exp"] < now:
        fails.append(now)
        return None
    return rec["wid"]


_CAR_PAIR_JS = r"""
const $ = s => document.querySelector(s);
function b64(buf){ return btoa(String.fromCharCode(...new Uint8Array(buf))); }
function idb(){ return new Promise((res, rej) => { const r = indexedDB.open('gate-access', 1);
  r.onupgradeneeded = () => r.result.createObjectStore('keys'); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); }); }
async function idbPut(k, v){ const db = await idb(); return new Promise((res, rej) => {
  const t = db.transaction('keys', 'readwrite'); t.objectStore('keys').put(v, k); t.oncomplete = () => res(); t.onerror = () => rej(t.error); }); }
let code = '';
function show(){ $('#code').textContent = (code + '______').slice(0, 6).replace(/(...)/, '$1 '); $('#go').disabled = code.length !== 6; }
document.querySelectorAll('.k').forEach(b => b.addEventListener('click', () => {
  const v = b.dataset.k; if (v === 'del') code = code.slice(0, -1); else if (code.length < 6) code += v; show(); }));
$('#go').addEventListener('click', async () => {
  const m = $('#msg'); $('#go').disabled = true; m.className = 'msg'; m.textContent = 'Łączę…';
  try {
    if (!window.crypto || !crypto.subtle || !window.indexedDB) throw new Error('Ta przeglądarka nie obsługuje bezpiecznych kluczy');
    const kp = await crypto.subtle.generateKey({name:'ECDSA', namedCurve:'P-256'}, false, ['sign','verify']);
    const pub = b64(await crypto.subtle.exportKey('spki', kp.publicKey));
    const r = await fetch(location.pathname + '?do=pair', {method:'POST', cache:'no-store',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({code, pub})});
    const j = await r.json().catch(() => ({}));
    if (!j.ok) throw new Error(j.msg || 'Nieprawidłowy kod');
    await idbPut(j.path, {key: kp.privateKey, pub, dev: j.dev});
    m.className = 'msg ok'; m.textContent = 'Połączono – ' + j.name;
    location.replace(j.path + '?app=1&car=1');
  } catch (e) { m.className = 'msg err'; m.textContent = e.message; code = ''; show(); }
});
show();
"""


def car_pair_page() -> str:
    keys = "".join(
        f"<button class='k' data-k='{k}'>{k}</button>" for k in "123456789"
    ) + "<button class='k' data-k='del'>⌫</button><button class='k' data-k='0'>0</button>" \
        "<button class='k ok' id='go' disabled>Połącz</button>"
    css = _APP_CSS + (
        "body{display:flex;flex-direction:column;align-items:center;padding:24px}"
        ".pad{display:grid;grid-template-columns:repeat(3,120px);gap:12px;margin-top:18px}"
        ".k{padding:26px 0;font-size:1.8rem;background:#1e2229;color:#e8e6df;border-color:#3a4049}"
        ".k.ok{background:#c8952f;color:#1a1206;font-size:1.2rem}"
        "#code{font-size:3rem;letter-spacing:.25em;font-weight:700;color:#ffcf7a;margin-top:8px}"
        ".msg{text-align:center;font-size:1.1rem}"
    )
    return (
        "<!doctype html><html lang='pl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='referrer' content='no-referrer'><meta name='robots' content='noindex,nofollow'>"
        f"<title>Brama – połącz samochód</title><style>{css}</style></head><body>"
        "<h1>Połącz samochód</h1>"
        "<div class='sub'>Na telefonie w aplikacji Brama dotknij „Połącz z samochodem” i wpisz tutaj kod.</div>"
        f"<div id='code'></div><div class='pad'>{keys}</div><div class='msg' id='msg'></div>"
        f"<script>{_CAR_PAIR_JS}</script></body></html>"
    )
