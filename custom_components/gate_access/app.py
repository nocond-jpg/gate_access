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
ICONS = {"icon-180": "icon-180.png", "icon-192": "icon-192.png", "icon-512": "icon-512.png"}


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


def manifest(path: str, person: str) -> str:
    return json.dumps(
        {
            "name": f"Brama – {person}",
            "short_name": "Brama",
            "id": path,
            "start_url": f"{path}?app=1",
            "scope": path,
            "display": "standalone",
            "background_color": "#14171c",
            "theme_color": "#14171c",
            "icons": [
                {"src": f"{path}?asset=icon-192", "sizes": "192x192", "type": "image/png"},
                {"src": f"{path}?asset=icon-512", "sizes": "512x512", "type": "image/png"},
            ],
        },
        ensure_ascii=False,
    )


SERVICE_WORKER = """
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
"""

_APP_JS = r"""
const P = location.pathname, VAPID = __VAPID__, FOCUS = new URLSearchParams(location.search).get('focus');
const $ = s => document.querySelector(s);
const standalone = window.navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;
const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
let busy = {};

function label(v){ return v === true ? ['Otwarta','open'] : v === false ? ['Zamknięta','closed'] : ['—','']; }
async function api(q, opt){ const r = await fetch(P + q, Object.assign({cache:'no-store'}, opt||{}));
  let j = {}; try { j = await r.json(); } catch(e) {} j._status = r.status; return j; }

async function refresh(){
  try {
    const j = await api('?do=state');
    if (!j.ok) { $('#list').innerHTML = '<div class="card">' + (j.msg || 'Brak dostępu') + '</div>'; return; }
    for (const t of j.targets) {
      const card = document.getElementById('c-' + t.id); if (!card) continue;
      const [txt, cls] = label(t.open); const st = card.querySelector('.st');
      st.textContent = txt; st.className = 'st ' + cls;
    }
  } catch (e) {}
}

async function act(id, action, btn){
  if (busy[id]) return; busy[id] = true;
  const card = document.getElementById('c-' + id), msg = card.querySelector('.msg');
  card.querySelectorAll('button').forEach(b => b.disabled = true);
  msg.className = 'msg'; msg.textContent = action === 'open' ? 'Otwieram…' : 'Zamykam…';
  try {
    const j = await api('?do=' + action + '&t=' + encodeURIComponent(id), {method:'POST'});
    if (j.ok) { msg.className = 'msg ok'; msg.textContent = action === 'open' ? 'Otwarto' : 'Zamknięto';
      if (navigator.vibrate) navigator.vibrate(40); }
    else { msg.className = 'msg err'; msg.textContent = j.msg || j.title || 'Nie udało się'; }
  } catch (e) { msg.className = 'msg err'; msg.textContent = 'Błąd połączenia'; }
  setTimeout(() => { busy[id] = false; card.querySelectorAll('button').forEach(b => b.disabled = false); }, 1500);
  setTimeout(refresh, 800); setTimeout(refresh, 4000);
}

function b64ToBytes(s){ s = s.replace(/-/g,'+').replace(/_/g,'/'); s += '='.repeat((4 - s.length % 4) % 4);
  const r = atob(s), o = new Uint8Array(r.length); for (let i=0;i<r.length;i++) o[i] = r.charCodeAt(i); return o; }

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
    $('#poff').onclick = async () => { await api('?do=unsubscribe', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({endpoint: sub.endpoint})}); await sub.unsubscribe(); pushUi(); };
    // keep server copy fresh (e.g. after HA restore)
    api('?do=subscribe', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(sub.toJSON())});
  } else {
    box.innerHTML = 'Przypomnienia o otwartej bramie: <b>wyłączone</b><button id="pon">Włącz przypomnienia</button>';
    $('#pon').onclick = async () => {
      try {
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') { box.insertAdjacentHTML('beforeend','<div class="hint">Brak zgody na powiadomienia (Ustawienia → Powiadomienia → Brama).</div>'); return; }
        const s = await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: b64ToBytes(VAPID)});
        const j = await api('?do=subscribe', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(s.toJSON())});
        if (!j.ok) throw new Error(j.msg || 'odrzucono');
        pushUi();
      } catch (e) { box.insertAdjacentHTML('beforeend','<div class="hint">Nie udało się: ' + e.message + '</div>'); }
    };
  }
}

document.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', () => act(b.dataset.id, b.dataset.act, b)));
if (FOCUS) { const c = document.getElementById('c-' + FOCUS); if (c) { c.classList.add('focus'); c.scrollIntoView(); } }
refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
pushUi().catch(() => {});
"""


def app_page(path: str, person: str, targets: list[dict], vapid_public: str) -> str:
    cards = []
    for t in targets:
        tid = html.escape(t["id"], quote=True)
        close_btn = (
            f"<button class='cl' data-act='close' data-id='{tid}'>Zamknij</button>"
            if t["can_close"] else ""
        )
        cards.append(
            f"<div class='card' id='c-{tid}'><div class='row'>"
            f"<span class='nm'>{html.escape(t['name'])}</span><span class='st'>—</span></div>"
            f"<div class='btns'><button class='op' data-act='open' data-id='{tid}'>Otwórz</button>"
            f"{close_btn}</div><div class='msg'></div></div>"
        )
    js = _APP_JS.replace("__VAPID__", json.dumps(vapid_public))
    p = html.escape(path, quote=True)
    return (
        "<!doctype html><html lang='pl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
        "<meta name='apple-mobile-web-app-capable' content='yes'>"
        "<meta name='mobile-web-app-capable' content='yes'>"
        "<meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'>"
        "<meta name='apple-mobile-web-app-title' content='Brama'>"
        "<meta name='theme-color' content='#14171c'>"
        "<meta name='referrer' content='no-referrer'>"
        "<meta name='robots' content='noindex,nofollow'>"
        f"<link rel='manifest' href='{p}?asset=manifest'>"
        f"<link rel='apple-touch-icon' href='{p}?asset=icon-180'>"
        f"<link rel='icon' type='image/png' href='{p}?asset=icon-192'>"
        f"<title>Brama</title><style>{_APP_CSS}</style></head><body>"
        f"<h1>Brama</h1><div class='sub'>{html.escape(person)}</div>"
        f"<div id='list'>{''.join(cards) or '<div class=card>Brak przypisanych obiektów.</div>'}</div>"
        "<div class='push' id='push'></div>"
        f"<script>{js}</script></body></html>"
    )
