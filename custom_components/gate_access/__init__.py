"""Gate Access (PoC) — per-person webhooks for multiple openings.

Supports several targets (gate, wicket, ...). A target can be a cover, lock,
switch, button or script; the open/close action is chosen from the entity's
domain. Each link is bound to one target. Links can be permanent, time-limited,
use-limited or one-time, editable after creation, and every open (or blocked
attempt) is recorded in a per-link history. An admin panel and a Lovelace card
create and manage links.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import secrets
import time
from datetime import datetime

import voluptuous as vol
from aiohttp import web

from homeassistant.components import panel_custom, webhook
from homeassistant.components.frontend import add_extra_js_url, async_remove_panel
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, Context, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADMIN_ONLY,
    CONF_APPROVE_NOTIFY,
    CONF_BASE_URL,
    CONF_CLOSE_AFTER,
    CONF_CLOSE_MAP,
    CONF_CONFIRM_OPEN,
    CONF_DELETE_PASSWORD,
    CONF_GATE_ENTITY,
    CONF_LOG_CLOSINGS,
    CONF_LOG_PATH,
    CONF_RATE_LIMIT,
    CONF_REMIND_MAP,
    CONF_SHOW_CLOSE,
    CONF_STATE_MAP,
    CONF_STATS,
    CONF_TARGETS,
    DEFAULT_ADMIN_ONLY,
    DEFAULT_CLOSE_AFTER,
    DEFAULT_CONFIRM_OPEN,
    DEFAULT_LOG_PATH,
    DEFAULT_RATE_LIMIT,
    DEFAULT_SHOW_CLOSE,
    APPROVE_ACTION_PREFIX,
    CAR_CODE_TTL,
    CAR_WEBHOOK_ID,
    DOMAIN,
    EVENT_OPENED,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL_PATH,
    STATIC_URL,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from . import app as gapp

_LOGGER = logging.getLogger(__name__)

_SLUG_MAP = str.maketrans("ąćęłńóśźż", "acelnoszz")
MAX_USES = 9999
HISTORY_MAX = 200
_ASSETS = {"panel.js", "card.js"}

# Per-domain "open" and "close" actions. A wicket is usually a lock/relay.
OPEN_ACTIONS = {
    "cover": ("cover", "open_cover"),
    "lock": ("lock", "unlock"),
    "switch": ("switch", "turn_on"),
    "input_boolean": ("input_boolean", "turn_on"),
    "button": ("button", "press"),
    "input_button": ("input_button", "press"),
    "script": ("script", "turn_on"),
    "light": ("light", "turn_on"),
}
CLOSE_ACTIONS = {
    "cover": ("cover", "close_cover"),
    "lock": ("lock", "lock"),
    "switch": ("switch", "turn_off"),
    "input_boolean": ("input_boolean", "turn_off"),
    "light": ("light", "turn_off"),
    # button / script have no meaningful "close"
}


def _cfg(entry: ConfigEntry, key: str, default=None):
    return entry.options.get(key, entry.data.get(key, default))


def _targets(entry: ConfigEntry) -> list[str]:
    """Configured target entity_ids, migrating the legacy single-gate key."""
    targets = _cfg(entry, CONF_TARGETS)
    if targets:
        return list(targets)
    legacy = _cfg(entry, CONF_GATE_ENTITY)
    return [legacy] if legacy else []


def _default_target(entry: ConfigEntry) -> str | None:
    targets = _targets(entry)
    return targets[0] if targets else None


def _user_targets(entry: ConfigEntry, user: dict | None) -> list[str]:
    """Openings a person can use in the app: app_targets, else the link's target."""
    configured = _targets(entry)
    if user:
        chosen = [t for t in (user.get("app_targets") or []) if t in configured]
        if chosen:
            return chosen
        if user.get("target") in configured:
            return [user["target"]]
    return configured[:1]


def _app_targets_info(hass: HomeAssistant, entry: ConfigEntry, user: dict) -> list[dict]:
    out = []
    for t in _user_targets(entry, user):
        st = hass.states.get(t)
        garage = bool(st and st.attributes.get("device_class") == "garage")
        out.append(
            {
                "id": t,
                "name": _target_name(hass, t),
                "can_close": t.split(".")[0] in CLOSE_ACTIONS,
                "kind": "garage" if garage else "icon",
            }
        )
    return out


def _app_path(webhook_id: str) -> str:
    return f"/api/webhook/{webhook_id}"


def _ua_label(ua: str) -> str:
    ua = ua or ""
    for key, label in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                       ("Macintosh", "Mac"), ("Windows", "Windows")):
        if key in ua:
            return label
    return "urządzenie"


def _asset_url(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), "frontend", name)
    try:
        version = int(os.path.getmtime(path))
    except OSError:
        version = 0
    return f"{STATIC_URL}/{name}?v={version}"


def _slug(name: str) -> str:
    s = name.strip().lower().translate(_SLUG_MAP)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "user"


def _new_webhook_id(name: str) -> str:
    return f"gate-{_slug(name)}-{secrets.token_hex(12)}"


def _valid_name(name: str) -> bool:
    return bool(re.fullmatch(r"[\w .\-]{1,40}", name or "", re.UNICODE))


SERVICE_LOG_OPEN = "log_open"
LOG_OPEN_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("source"): cv.string,
        vol.Optional("target"): cv.string,
    }
)


def _append_log(
    path: str, name: str, target_name: str, source: str, status: str
) -> None:
    try:
        os.makedirs(os.path.dirname(path) or "/", exist_ok=True)
    except OSError:
        pass
    verb = "otworzył(a)" if status == "opened" else "zamknął(ęła)"
    line = (
        f"{datetime.now():%Y-%m-%d %H:%M} — {name} {verb}: "
        f"{target_name} [{source}]\n"
    )
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line)


def _parse_ttl(body: dict) -> tuple[str | None, str | None, int | None]:
    starts_at = body.get("starts_at")
    start = None
    if starts_at is not None:
        start = dt_util.parse_datetime(starts_at)
        if start is None:
            raise ValueError("Nieprawidłowa data początkowa")
        starts_at = start.isoformat()

    expires_at = body.get("expires_at")
    if expires_at is not None:
        end = dt_util.parse_datetime(expires_at)
        if end is None:
            raise ValueError("Nieprawidłowa data wygaśnięcia")
        if end <= dt_util.utcnow():
            raise ValueError("Data wygaśnięcia jest w przeszłości")
        if start is not None and end <= start:
            raise ValueError("Data końcowa musi być po początkowej")
        expires_at = end.isoformat()

    uses_total = body.get("uses_total")
    if uses_total is not None:
        try:
            uses_total = int(uses_total)
        except (TypeError, ValueError):
            raise ValueError("Liczba użyć musi być liczbą")
        if uses_total < 1 or uses_total > MAX_USES:
            raise ValueError(f"Liczba użyć musi być w zakresie 1–{MAX_USES}")
    return starts_at, expires_at, uses_total


# ---------------------------------------------------------------------------
# Expiry (TTL)
# ---------------------------------------------------------------------------
def _activity(user: dict) -> str:
    """One of: disabled | pending | expired | active."""
    if not user.get("enabled", True):
        return "disabled"
    now = dt_util.utcnow()
    sa = user.get("starts_at")
    if sa:
        start = dt_util.parse_datetime(sa)
        if start and now < start:
            return "pending"
    ea = user.get("expires_at")
    if ea:
        end = dt_util.parse_datetime(ea)
        if end and now >= end:
            return "expired"
    left = user.get("uses_left")
    if left is not None and left <= 0:
        return "expired"
    return "active"


def _is_expired(user: dict) -> bool:
    return _activity(user) == "expired"


@callback
def _rate_ok(hass: HomeAssistant, entry: ConfigEntry, webhook_id: str) -> bool:
    """Allow at most CONF_RATE_LIMIT opens per link per rolling 60 seconds."""
    try:
        limit = int(_cfg(entry, CONF_RATE_LIMIT, DEFAULT_RATE_LIMIT) or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0:
        return True
    now = time.monotonic()
    rate = hass.data[DOMAIN][entry.entry_id].setdefault("rate", {})
    hits = [t for t in rate.get(webhook_id, []) if now - t < 60]
    if len(hits) >= limit:
        rate[webhook_id] = hits
        return False
    hits.append(now)
    rate[webhook_id] = hits
    return True


def _status(user: dict) -> dict:
    act = _activity(user)
    return {
        "activity": act,
        "expired": act == "expired",
        "enabled": user.get("enabled", True),
        "starts_at": user.get("starts_at"),
        "expires_at": user.get("expires_at"),
        "uses_left": user.get("uses_left"),
        "uses_total": user.get("uses_total"),
    }


def _target_name(hass: HomeAssistant, entity_id: str | None) -> str:
    if not entity_id:
        return "brama"
    state = hass.states.get(entity_id)
    if state and state.attributes.get("friendly_name"):
        return state.attributes["friendly_name"]
    return entity_id


_GATE_SVG = (
    "<svg viewBox='0 0 24 24' width='56' height='56' fill='#c8952f'>"
    "<path d='M12,2A2,2 0 0,0 10,4V4.5H4.5A2.5,2.5 0 0,0 2,7V19H4V17H20V19H22V7A2.5,2.5 0 0,0 "
    "19.5,4.5H14V4A2,2 0 0,0 12,2M4.5,6.5H10V8.5H4V7A0.5,0.5 0 0,1 4.5,6.5M14,6.5H19.5A0.5,"
    "0.5 0 0,1 20,7V8.5H14V6.5M4,10.5H10V12.5H4V10.5M14,10.5H20V12.5H14V10.5M4,14.5H10V16.5H4"
    "V14.5M14,14.5H20V16.5H14V14.5Z' /></svg>"
)

# Favicons (inline SVG data URIs) — a plain gate and a garage door.
_GATE_ICON = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='#c8952f'>"
    "<path d='M12,2A2,2 0 0,0 10,4V4.5H4.5A2.5,2.5 0 0,0 2,7V19H4V17H20V19H22V7A2.5,2.5 0 0,0 "
    "19.5,4.5H14V4A2,2 0 0,0 12,2M4.5,6.5H10V8.5H4V7A0.5,0.5 0 0,1 4.5,6.5M14,6.5H19.5A0.5,"
    "0.5 0 0,1 20,7V8.5H14V6.5M4,10.5H10V12.5H4V10.5M14,10.5H20V12.5H14V10.5M4,14.5H10V16.5H4"
    "V14.5M14,14.5H20V16.5H14V14.5Z'/></svg>"
)
_GARAGE_ICON = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='#c8952f'>"
    "<path d='M22,9V20H19V11H5V20H2V9L12,5L22,9M18,12H6V13.5H18V12M18,15H6V16.5H18V15M18,18H6"
    "V19.5H18V18Z'/></svg>"
)


def _favicon_tag(svg: str) -> str:
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return (
        "<link rel='icon' type='image/svg+xml' "
        f"href='data:image/svg+xml;base64,{b64}'>"
    )


_FAVICON_GATE = _favicon_tag(_GATE_ICON)
_FAVICON_GARAGE = _favicon_tag(_GARAGE_ICON)


def _favicon(hass: HomeAssistant, target: str | None) -> str:
    dclass = None
    if target:
        st = hass.states.get(target)
        if st:
            dclass = st.attributes.get("device_class")
    return _FAVICON_GARAGE if dclass == "garage" else _FAVICON_GATE

_PAGE_CSS = (
    "body{margin:0;height:100vh;display:grid;place-items:center;"
    "font-family:system-ui,sans-serif;background:#14171c;color:#e8e6df}"
    ".card{text-align:center}h1{font-weight:600;font-size:1.4rem;margin:.6rem 0 .2rem}"
    "p{color:#8b9099;margin:0}"
    "button.act{margin-top:22px;border:1px solid #c8952f;border-radius:12px;"
    "padding:14px 22px;font-size:1.05rem;font-weight:600;cursor:pointer;min-width:230px;"
    "background:#c8952f;color:#1a1206}"
    "button.close{background:transparent;color:#e8b45a}"
    "button.close:hover{background:#c8952f;color:#1a1206}"
    "button.busy{opacity:.6;cursor:wait}"
)


def _confirm_page(show_close: bool, close_secs: int, favicon: str = "") -> str:
    """Landing page: visiting does NOT open; the gate opens only on tap (iOS-safe)."""
    return (
        "<!doctype html><html lang='pl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"{favicon}<title>Brama</title><style>" + _PAGE_CSS + "</style></head><body>"
        "<div class='card'>" + _GATE_SVG +
        "<h1 id='ttl'>Brama</h1><p id='sub'>Naciśnij, aby otworzyć.</p>"
        "<button id='obtn' class='act' onclick='openGate()'>Otwórz bramę</button>"
        "<button id='cbtn' class='act close' style='display:none' onclick='closeGate()'>Zamknij bramę</button>"
        "<script>"
        f"var showClose={'true' if show_close else 'false'};var secs={close_secs};"
        "var ob=document.getElementById('obtn');var cb=document.getElementById('cbtn');"
        "var ttl=document.getElementById('ttl');var sub=document.getElementById('sub');"
        "var busy=false;var t=null;"
        "function fmt(s){var m=Math.floor(s/60);var x=s%60;return m+':' + (x<10?'0':'') + x;}"
        "async function openGate(){ if(busy)return;busy=true;ob.classList.add('busy');ob.textContent='Otwieram…';"
        "try{ var r=await fetch(window.location.pathname+'?do=open',{method:'POST'});"
        "var j=await r.json();"
        "if(j.ok){ ttl.textContent='Otwarto'+(j.name?': '+j.name:''); sub.textContent='';"
        "ob.style.display='none'; if(showClose||secs>0){ cb.style.display=''; startC(); } }"
        "else{ ttl.textContent=j.title||'Nie otwarto'; sub.textContent=j.msg||'';"
        "ob.classList.remove('busy'); ob.textContent='Otwórz bramę'; } }"
        "catch(e){ sub.textContent='Błąd połączenia.'; ob.classList.remove('busy'); ob.textContent='Otwórz bramę'; }"
        "busy=false; }"
        "function startC(){ if(secs<=0){cb.textContent='Zamknij bramę';return;} tickC(); t=setInterval(tickC,1000); }"
        "function tickC(){ if(busy)return; if(secs>0){cb.textContent='Zamknij (auto za '+fmt(secs)+')';secs--;}"
        "else{cb.textContent='Zamknij bramę'; if(t){clearInterval(t);t=null;}} }"
        "async function closeGate(){ if(busy)return;busy=true; if(t){clearInterval(t);t=null;}"
        "cb.classList.add('busy'); cb.textContent='Zamykam…';"
        "try{ var r=await fetch(window.location.pathname+'?do=close',{method:'POST'});"
        "cb.textContent = r.ok ? 'Zamknięto — kliknij, aby zamknąć ponownie' : 'Błąd — spróbuj ponownie'; }"
        "catch(e){ cb.textContent='Błąd — spróbuj ponownie'; }"
        "setTimeout(function(){busy=false;cb.classList.remove('busy');},1500); }"
        "</script></div></body></html>"
    )


def _open_html(
    tname: str, sub: str, show_close: bool, close_secs: int, favicon: str = ""
) -> str:
    """Success page after opening, optionally with a close button + countdown."""
    icon = (
        "<svg viewBox='0 0 24 24' width='56' height='56' fill='#c8952f'>"
        "<path d='M12,2A2,2 0 0,0 10,4V4.5H4.5A2.5,2.5 0 0,0 2,7V19H4V17H20V19H22V7A2.5,2.5 0 0,0 "
        "19.5,4.5H14V4A2,2 0 0,0 12,2M4.5,6.5H10V8.5H4V7A0.5,0.5 0 0,1 4.5,6.5M14,6.5H19.5A0.5,"
        "0.5 0 0,1 20,7V8.5H14V6.5M4,10.5H10V12.5H4V10.5M14,10.5H20V12.5H14V10.5M4,14.5H10V16.5H4"
        "V14.5M14,14.5H20V16.5H14V14.5Z' /></svg>"
    )
    button = ""
    script = ""
    if show_close or close_secs > 0:
        button = "<button id='cbtn' onclick='closeGate()'>Zamknij bramę</button>"
        script = (
            "<script>"
            f"var secs={close_secs};"
            "var btn=document.getElementById('cbtn');"
            "var busy=false;var t=null;"
            "function fmt(s){var m=Math.floor(s/60);var x=s%60;"
            "return m+':' + (x<10?'0':'') + x;}"
            "function tick(){ if(busy)return; if(secs>0){"
            "btn.textContent='Zamknij (auto za '+fmt(secs)+')'; secs--; }"
            "else { btn.textContent='Zamknij bramę'; if(t){clearInterval(t);t=null;} } }"
            "if(secs>0){ tick(); t=setInterval(tick,1000); }"
            "async function closeGate(){ if(busy)return; busy=true;"
            "if(t){clearInterval(t);t=null;} btn.classList.add('busy');"
            "btn.textContent='Zamykam…';"
            "try{ var r=await fetch(window.location.pathname+'?do=close',{method:'POST'});"
            "btn.textContent = r.ok ? 'Zamknięto — kliknij, aby zamknąć ponownie'"
            " : 'Błąd — spróbuj ponownie'; }"
            "catch(e){ btn.textContent='Błąd — spróbuj ponownie'; }"
            "setTimeout(function(){ busy=false; btn.classList.remove('busy'); },1500); }"
            "</script>"
        )
    return (
        "<!doctype html><html lang='pl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"{favicon}<title>Brama</title><style>"
        "body{margin:0;height:100vh;display:grid;place-items:center;"
        "font-family:system-ui,sans-serif;background:#14171c;color:#e8e6df}"
        ".card{text-align:center}h1{font-weight:600;font-size:1.4rem;margin:.6rem 0 .2rem}"
        "p{color:#8b9099;margin:0}"
        "#cbtn{margin-top:22px;background:transparent;border:1px solid #c8952f;"
        "color:#e8b45a;border-radius:12px;padding:12px 20px;font-size:1rem;font-weight:600;"
        "cursor:pointer;min-width:220px}"
        "#cbtn:hover{background:#c8952f;color:#1a1206}"
        "#cbtn.busy{opacity:.6;cursor:wait}"
        "</style></head><body><div class='card'>"
        f"{icon}<h1>Otwarto: {tname}</h1><p>{sub}</p>{button}"
        f"</div>{script}</body></html>"
    )


def _html(name: str, sub: str, dim: bool = False, favicon: str = "") -> str:
    style = "opacity:.5" if dim else ""
    icon = (
        "<svg viewBox='0 0 24 24' width='56' height='56' fill='#c8952f' style='" + style + "'>"
        "<path d='M12,2A2,2 0 0,0 10,4V4.5H4.5A2.5,2.5 0 0,0 2,7V19H4V17H20V19H22V7A2.5,2.5 0 0,0 "
        "19.5,4.5H14V4A2,2 0 0,0 12,2M4.5,6.5H10V8.5H4V7A0.5,0.5 0 0,1 4.5,6.5M14,6.5H19.5A0.5,"
        "0.5 0 0,1 20,7V8.5H14V6.5M4,10.5H10V12.5H4V10.5M14,10.5H20V12.5H14V10.5M4,14.5H10V16.5H4"
        "V14.5M14,14.5H20V16.5H14V14.5Z' /></svg>"
    )
    return (
        "<!doctype html><html lang='pl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"{favicon}<title>Brama</title><style>"
        "body{margin:0;height:100vh;display:grid;place-items:center;"
        "font-family:system-ui,sans-serif;background:#14171c;color:#e8e6df}"
        ".card{text-align:center}"
        "h1{font-weight:600;font-size:1.4rem;margin:.6rem 0 .2rem}"
        "p{color:#8b9099;margin:0}</style></head><body><div class='card'>"
        f"{icon}<h1>{name}</h1><p>{sub}</p>"
        "</div></body></html>"
    )


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
@callback
def _add_history(
    hass: HomeAssistant,
    entry: ConfigEntry,
    name: str,
    webhook_id: str | None,
    status: str,
    target: str | None,
    source: str = "link",
) -> None:
    hist = hass.data[DOMAIN][entry.entry_id]["history"]
    hist.insert(
        0,
        {
            "id": secrets.token_hex(6),
            "name": name,
            "webhook_id": webhook_id,
            "ts": dt_util.utcnow().isoformat(timespec="seconds"),
            "status": status,  # "opened" | "expired" | "closed"
            "target": target,
            "source": source,  # link | panel | ha | auto | <custom>
        },
    )
    del hist[HISTORY_MAX:]


async def _maybe_record_rate(
    hass: HomeAssistant,
    entry: ConfigEntry,
    name: str,
    webhook_id: str,
    target: str | None,
) -> None:
    """Record a rate-limited attempt, but at most once per link per minute."""
    hist = hass.data[DOMAIN][entry.entry_id]["history"]
    now = dt_util.utcnow()
    for h in hist[:15]:
        if h.get("webhook_id") == webhook_id and h.get("status") == "rate":
            ts = dt_util.parse_datetime(h.get("ts", "") or "")
            if ts and (now - ts).total_seconds() < 60:
                return
    _add_history(hass, entry, name, webhook_id, "rate", target, "link")
    await _save(hass, entry)


async def _delete_history(
    hass: HomeAssistant, entry: ConfigEntry, entry_id: str | None
) -> int:
    hist = hass.data[DOMAIN][entry.entry_id]["history"]
    if entry_id is None:
        removed = len(hist)
        hist.clear()
    else:
        before = len(hist)
        hist[:] = [h for h in hist if h.get("id") != entry_id]
        removed = before - len(hist)
    await _save(hass, entry)
    return removed


@callback
def _bump_stats(hass: HomeAssistant, entry: ConfigEntry, entity_id: str) -> None:
    if not bool(_cfg(entry, CONF_STATS, False)):
        return
    stats = hass.data[DOMAIN][entry.entry_id]["stats"]
    now = dt_util.now()
    dk = now.strftime("%Y-%m-%d")
    mk = now.strftime("%Y-%m")
    yk = now.strftime("%Y")
    ent = stats.setdefault(entity_id, {"day": {}, "month": {}, "year": {}, "total": 0})
    ent["day"][dk] = ent["day"].get(dk, 0) + 1
    ent["month"][mk] = ent["month"].get(mk, 0) + 1
    ent["year"][yk] = ent["year"].get(yk, 0) + 1
    ent["total"] = ent.get("total", 0) + 1
    if len(ent["day"]) > 120:  # keep the store bounded
        for old_key in sorted(ent["day"])[:-120]:
            del ent["day"][old_key]


async def _record(
    hass: HomeAssistant,
    entry: ConfigEntry,
    name: str,
    target: str | None,
    source: str,
    status: str = "opened",
    webhook_id: str | None = None,
) -> str:
    """Log + history + event + stats for an open/close (does not actuate)."""
    tname = _target_name(hass, target)
    await hass.async_add_executor_job(
        _append_log,
        _cfg(entry, CONF_LOG_PATH, DEFAULT_LOG_PATH),
        name,
        tname,
        source,
        status,
    )
    hass.bus.async_fire(
        EVENT_OPENED,
        {
            "name": name,
            "webhook_id": webhook_id,
            "target": target,
            "source": source,
            "status": status,
        },
    )
    _add_history(hass, entry, name, webhook_id, status, target, source)
    if status == "opened" and target:
        _bump_stats(hass, entry, target)
    await _save(hass, entry)
    return tname


async def _record_open(
    hass: HomeAssistant,
    entry: ConfigEntry,
    name: str,
    target: str | None,
    source: str,
    webhook_id: str | None = None,
) -> str:
    return await _record(hass, entry, name, target, source, "opened", webhook_id)


@callback
def _mark_own(hass: HomeAssistant, entry: ConfigEntry, ctx_id: str) -> None:
    """Remember a context we initiated, so the state listener can skip it."""
    store = hass.data[DOMAIN][entry.entry_id].setdefault("own_ctx", {})
    now = time.monotonic()
    store[ctx_id] = now
    for key, ts in list(store.items()):
        if now - ts > 15:
            del store[key]


@callback
def _is_own(hass: HomeAssistant, entry: ConfigEntry, ctx) -> bool:
    if not ctx:
        return False
    store = hass.data[DOMAIN][entry.entry_id].get("own_ctx", {})
    if ctx.id in store:
        return True
    return bool(ctx.parent_id and ctx.parent_id in store)


def _close_secs(entry: ConfigEntry, entity_id: str | None) -> int:
    """Auto-close seconds for a target (per-target map, legacy value as fallback)."""
    if not entity_id:
        return 0
    cmap = _cfg(entry, CONF_CLOSE_MAP, {}) or {}
    if entity_id in cmap:
        try:
            return int(cmap[entity_id] or 0)
        except (TypeError, ValueError):
            return 0
    try:
        return int(_cfg(entry, CONF_CLOSE_AFTER, DEFAULT_CLOSE_AFTER) or 0)
    except (TypeError, ValueError):
        return 0


@callback
def _schedule_auto_close(
    hass: HomeAssistant, entry: ConfigEntry, entity_id: str, domain: str
) -> None:
    """Schedule an auto-close for any open (webhook, panel, pilot, automation)."""
    secs = _close_secs(entry, entity_id)
    closer = CLOSE_ACTIONS.get(domain)
    if secs <= 0 or not closer:
        return

    async def _auto_close(_now, _svc=closer, _eid=entity_id):
        state = hass.states.get(_eid)
        if state and state.state in ("closed", "closing", "off", "locked"):
            return  # already closed
        await hass.services.async_call(
            _svc[0], _svc[1], {"entity_id": _eid}, blocking=False
        )

    async_call_later(hass, secs, _auto_close)


async def _do_open(
    hass: HomeAssistant,
    entry: ConfigEntry,
    target: str | None,
    name: str,
    webhook_id: str | None,
    source: str = "link",
) -> str:
    """Actuate a target, then record it. Auto-close is scheduled by the listener."""
    domain = target.split(".")[0] if target else ""
    opener = OPEN_ACTIONS.get(domain)
    if target and opener:
        ctx = Context()
        _mark_own(hass, entry, ctx.id)
        await hass.services.async_call(
            opener[0], opener[1], {"entity_id": target}, blocking=False, context=ctx
        )
    return await _record_open(hass, entry, name, target, source, webhook_id)


async def _do_close(
    hass: HomeAssistant,
    entry: ConfigEntry,
    target: str | None,
    name: str,
    source: str = "link",
) -> str:
    """Close a target (open-page button or panel), logged with the given source."""
    domain = target.split(".")[0] if target else ""
    closer = CLOSE_ACTIONS.get(domain)
    if target and closer:
        ctx = Context()
        _mark_own(hass, entry, ctx.id)
        await hass.services.async_call(
            closer[0], closer[1], {"entity_id": target}, blocking=False, context=ctx
        )
    return await _record(hass, entry, name, target, source, "closed")


# ---------------------------------------------------------------------------
# Webhook handling
# ---------------------------------------------------------------------------
async def _notify_new_device(
    hass: HomeAssistant, entry: ConfigEntry, user: dict, dev: dict
) -> None:
    """Ask the administrator to approve a new device (actionable notification)."""
    from homeassistant.components import persistent_notification

    title = "Brama – nowe urządzenie"
    msg = f"{user['name']}: {dev['label']}. Zatwierdzić dostęp z tego urządzenia?"
    persistent_notification.async_create(
        hass,
        msg + "\n\nZatwierdź w panelu „Brama – dostęp” → Dostępy → osoba → Aplikacja na telefon.",
        title=title,
        notification_id=f"gate_access_dev_{dev['id']}",
    )
    svc = (_cfg(entry, CONF_APPROVE_NOTIFY, "") or "").strip()
    if not svc.startswith("notify.") or not hass.services.has_service("notify", svc[7:]):
        if svc:
            _LOGGER.warning("Gate Access: usługa %s nie istnieje", svc)
        return
    try:
        await hass.services.async_call(
            "notify",
            svc[7:],
            {
                "title": title,
                "message": msg,
                "data": {
                    "tag": f"gate-dev-{dev['id']}",
                    "actions": [
                        {"action": f"{APPROVE_ACTION_PREFIX}APPROVE_{dev['id']}", "title": "Zatwierdź"},
                        {"action": f"{APPROVE_ACTION_PREFIX}REJECT_{dev['id']}", "title": "Odrzuć"},
                    ],
                },
            },
            blocking=False,
        )
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Gate Access: nie wysłano powiadomienia %s: %s", svc, err)


async def async_device_action(
    hass: HomeAssistant, entry: ConfigEntry, dev_id: str, op: str
) -> dict | None:
    """approve | reject | remove a device; returns the owning user."""
    from homeassistant.components import persistent_notification

    data = hass.data[DOMAIN][entry.entry_id]
    for user in data["users"]:
        dev = gapp.find_device(user, dev_id)
        if dev is None:
            continue
        if op == "approve":
            dev["status"] = "approved"
        else:
            user["devices"] = [d for d in user["devices"] if d["id"] != dev_id]
            user["push_subs"] = [s for s in user.get("push_subs", []) if s.get("dev") != dev_id]
        persistent_notification.async_dismiss(hass, f"gate_access_dev_{dev_id}")
        _add_history(
            hass, entry, user["name"], user["webhook_id"],
            "dev_approved" if op == "approve" else "dev_removed", None, "admin",
        )
        await _save(hass, entry)
        return user
    return None


_ACT_MSG = {
    "disabled": "Ten dostęp został wyłączony.",
    "pending": "Dostęp jeszcze nieaktywny.",
    "expired": "Ten dostęp nie jest już aktywny.",
}


def _make_handler(hass: HomeAssistant, entry: ConfigEntry):
    async def _handler(hass_: HomeAssistant, webhook_id: str, request: web.Request):
        data = hass.data[DOMAIN][entry.entry_id]
        user = next(
            (u for u in data["users"] if u["webhook_id"] == webhook_id), None
        )
        name = user["name"] if user else "nieznany"
        target = (user.get("target") if user else None) or _default_target(entry)

        act = _activity(user) if user else "active"
        confirm = bool(_cfg(entry, CONF_CONFIRM_OPEN, DEFAULT_CONFIRM_OPEN))
        show_close = bool(_cfg(entry, CONF_SHOW_CLOSE, DEFAULT_SHOW_CLOSE))
        close_secs = _close_secs(entry, target)
        fav = _favicon(hass, target)
        do = request.query.get("do")
        path = _app_path(webhook_id)

        # ---- mobile web app (v1.7) -------------------------------------
        asset = request.query.get("asset")
        if asset:
            if user is None:
                return web.Response(status=404)
            if asset == "manifest":
                tl = _app_targets_info(hass, entry, user)
                solo_t = next((t for t in tl if t["id"] == request.query.get("solo")), None)
                return web.Response(
                    text=gapp.manifest(path, name, tl, solo_t),
                    content_type="application/manifest+json",
                    headers={"Cache-Control": "no-cache"},
                )
            if asset == "sw":
                return web.Response(
                    text=gapp.SERVICE_WORKER,
                    content_type="application/javascript",
                    headers={"Cache-Control": "no-cache"},
                )
            body = await hass.async_add_executor_job(gapp.read_icon, asset)
            if body is None:
                return web.Response(status=404)
            return web.Response(
                body=body, content_type="image/png",
                headers={"Cache-Control": "public, max-age=86400"},
            )

        if request.query.get("app"):
            if user is None:
                return web.Response(status=404)
            if act != "active":
                return web.Response(
                    text=_html("Brak dostępu", _ACT_MSG.get(act, ""), True, fav),
                    content_type="text/html", status=403,
                )
            tl = _app_targets_info(hass, entry, user)
            solo_t = None
            if request.query.get("solo"):
                solo_t = next((t for t in tl if t["id"] == request.query["solo"]), None)
                if solo_t is None:
                    return web.Response(
                        text=_html(
                            "Ikona nieaktualna",
                            "Nie masz już dostępu do tego obiektu. Usuń tę ikonę z ekranu.",
                            True, fav,
                        ),
                        content_type="text/html", status=403,
                    )
            car = request.query.get("car") == "1" or "Tesla" in request.headers.get("User-Agent", "")
            return web.Response(
                text=gapp.app_page(path, name, tl, data["vapid"]["public"], solo_t, car=car and solo_t is None),
                content_type="text/html",
                headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                         "Referrer-Policy": "no-referrer"},
            )

        app_ops = ("register", "devstatus", "challenge", "state", "subscribe", "unsubscribe")
        if do in app_ops:
            if user is None:
                return web.json_response({"ok": False}, status=404)
            if act != "active":
                return web.json_response(
                    {"ok": False, "msg": _ACT_MSG.get(act, "Brak dostępu")}, status=403
                )
            if request.method != "POST":
                return web.json_response({"ok": False}, status=405)
            try:
                payload = await request.json()
            except ValueError:
                payload = None
            if not isinstance(payload, dict):
                return web.json_response({"ok": False}, status=400)

            if do == "register":
                if not _rate_ok(hass, entry, f"{webhook_id}|register"):
                    return web.json_response(
                        {"ok": False, "msg": "Za dużo prób – spróbuj za minutę"}, status=429
                    )
                dev, err = gapp.register_device(user, payload.get("pub"), payload.get("label"))
                if dev is None:
                    return web.json_response({"ok": False, "msg": err}, status=400)
                await _save(hass, entry)
                if dev["status"] == "pending" and not dev.get("notified"):
                    dev["notified"] = True
                    await _notify_new_device(hass, entry, user, dev)
                    await _save(hass, entry)
                return web.json_response({"ok": True, "dev": dev["id"], "status": dev["status"]})

            dev = gapp.find_device(user, payload.get("dev"))
            if do == "devstatus":
                return web.json_response(
                    {"ok": True, "status": dev["status"] if dev else "unknown"},
                    headers={"Cache-Control": "no-store"},
                )
            if dev is None or dev["status"] != "approved":
                return web.json_response(
                    {
                        "ok": False,
                        "status": dev["status"] if dev else "unknown",
                        "msg": "Urządzenie nie jest zatwierdzone",
                    },
                    status=403,
                )
            if do == "challenge":
                nonce = gapp.issue_nonce(data.setdefault("nonces", {}), dev["id"])
                if nonce is None:
                    return web.json_response({"ok": False, "msg": "Za dużo prób"}, status=429)
                return web.json_response({"ok": True, "nonce": nonce})
            if do == "state":
                return web.json_response(
                    {
                        "ok": True,
                        "targets": [
                            {"id": t, "open": gapp.is_open(hass, entry, t)}
                            for t in _user_targets(entry, user)
                        ],
                    },
                    headers={"Cache-Control": "no-store"},
                )
            if do == "subscribe":
                subscription = gapp.valid_subscription(payload)
                if subscription is None:
                    return web.json_response(
                        {"ok": False, "msg": "Nieprawidłowa subskrypcja"}, status=400
                    )
                gapp.add_subscription(user, subscription, dev["label"])
                for sub_rec in user["push_subs"]:
                    if sub_rec["endpoint"] == subscription["endpoint"]:
                        sub_rec["dev"] = dev["id"]
            else:
                endpoint = payload.get("endpoint", "")
                gapp.remove_subscription(user, endpoint if isinstance(endpoint, str) else "")
            await _save(hass, entry)
            return web.json_response({"ok": True})

        # Car pairing code (1.9): signed request from an approved device of this person
        if do == "carcode":
            if user is None or act != "active" or request.method != "POST":
                return web.json_response({"ok": False, "msg": "Brak dostępu"}, status=403)
            try:
                payload = await request.json()
            except ValueError:
                payload = None
            dev, err = gapp.verify_action(
                data.setdefault("nonces", {}), user, webhook_id, payload, "carcode", "car"
            )
            if dev is None:
                return web.json_response({"ok": False, "msg": err}, status=403)
            code = gapp.issue_car_code(data.setdefault("car_codes", {}), webhook_id)
            if code is None:
                return web.json_response(
                    {"ok": False, "msg": "Za dużo aktywnych kodów – odczekaj 5 minut"}, status=429
                )
            base = (_cfg(entry, CONF_BASE_URL, "") or "").rstrip("/") or str(request.url.origin())
            host = base.split("://", 1)[-1]
            return web.json_response(
                {"ok": True, "code": code, "ttl": CAR_CODE_TTL,
                 "url": f"{host}{_app_path(CAR_WEBHOOK_ID)}"},
                headers={"Cache-Control": "no-store"},
            )

        # A specific opening chosen in the app (?t=<entity_id>) → JSON, signed device only.
        # Plain single links (no ?t=) keep working exactly as before.
        req_t = request.query.get("t")
        want_json = req_t is not None
        if req_t is not None:
            if user is None or req_t not in _user_targets(entry, user):
                return web.json_response(
                    {"ok": False, "msg": "Brak dostępu do tego obiektu"}, status=403
                )
            if act != "active":
                return web.json_response(
                    {"ok": False, "msg": _ACT_MSG.get(act, "Brak dostępu")}, status=403
                )
            if do not in ("open", "close") or request.method != "POST":
                return web.json_response({"ok": False, "msg": "Nieprawidłowe żądanie"}, status=400)
            try:
                payload = await request.json()
            except ValueError:
                payload = None
            dev, err = gapp.verify_action(
                data.setdefault("nonces", {}), user, webhook_id, payload, do, req_t
            )
            if dev is None:
                _LOGGER.warning("Gate Access: odrzucono niepodpisane/nieważne żądanie (%s)", name)
                status = gapp.find_device(user, (payload or {}).get("dev")) if isinstance(payload, dict) else None
                return web.json_response(
                    {"ok": False, "msg": err, "status": status["status"] if status else "unknown"},
                    status=403,
                )
            target = req_t

        # Close action from a page button (?do=close)
        if do == "close":
            if user is None:
                return web.Response(text="", status=404)
            if want_json and act != "active":
                return web.json_response(
                    {"ok": False, "msg": _ACT_MSG.get(act, "Brak dostępu")}, status=403
                )
            if not user.get("enabled", True):
                return web.Response(text="off", status=403)
            if want_json and target.split(".")[0] not in CLOSE_ACTIONS:
                return web.json_response(
                    {"ok": False, "msg": "Tego obiektu nie można zamknąć"}, status=400
                )
            await _do_close(hass, entry, target, name)
            gapp.cancel_reminder(hass, entry, target)
            if want_json:
                return web.json_response({"ok": True})
            return web.Response(text="ok", content_type="text/plain")

        if want_json and do != "open":
            return web.json_response({"ok": False, "msg": "Użyj ?do=open"}, status=400)

        # iOS-safe mode: visiting the link only shows a page; open needs a tap.
        if confirm and do != "open":
            return web.Response(
                text=_confirm_page(show_close, close_secs, fav),
                content_type="text/html",
            )

        if user and act != "active":
            if want_json:
                _add_history(hass, entry, name, webhook_id, act, target, "app")
                await _save(hass, entry)
                return web.json_response(
                    {"ok": False, "msg": _ACT_MSG.get(act, "Brak dostępu")}, status=403
                )
            if act == "disabled":
                head, sub = "Dostęp wyłączony", "Ten dostęp został wyłączony."
            elif act == "pending":
                start = dt_util.parse_datetime(user.get("starts_at") or "")
                when = (
                    dt_util.as_local(start).strftime("%Y-%m-%d %H:%M")
                    if start else "później"
                )
                head, sub = "Dostęp jeszcze nieaktywny", f"Aktywny od {when}."
            else:
                head, sub = "Link wygasł", "Ten dostęp nie jest już aktywny."
            _add_history(hass, entry, name, webhook_id, act, target, "link")
            await _save(hass, entry)
            if confirm:
                return web.json_response({"ok": False, "title": head, "msg": sub})
            return web.Response(
                text=_html(head, sub, True, fav), content_type="text/html", status=403
            )

        # app: limit per opening, so gate + wicket in a row are not blocked
        rate_key = f"{webhook_id}|{target}" if want_json else webhook_id
        if user and not _rate_ok(hass, entry, rate_key):
            await _maybe_record_rate(hass, entry, name, webhook_id, target)
            if confirm or want_json:
                return web.json_response(
                    {"ok": False, "title": "Za dużo prób", "msg": "Spróbuj ponownie za chwilę."}
                )
            return web.Response(
                text=_html("Za dużo prób", "Spróbuj ponownie za chwilę.", True, fav),
                content_type="text/html",
                status=429,
            )

        if user and user.get("uses_left") is not None:
            user["uses_left"] = max(0, user["uses_left"] - 1)

        tname = await _do_open(
            hass, entry, target, name, webhook_id, source="app" if want_json else "link"
        )
        if user:
            gapp.schedule_reminder(
                hass, entry, target, webhook_id, f"{path}?app=1", tname
            )
        if confirm or want_json:
            return web.json_response({"ok": True, "name": tname})
        return web.Response(
            text=_open_html(
                tname, f"{name} · {datetime.now():%H:%M}", show_close, close_secs, fav
            ),
            content_type="text/html",
        )

    return _handler


def _make_car_handler(hass: HomeAssistant, entry: ConfigEntry):
    """Short pairing address for car browsers: GET = keypad, POST ?do=pair = redeem code."""

    async def _handler(hass_: HomeAssistant, webhook_id: str, request: web.Request):
        data = hass.data[DOMAIN].get(entry.entry_id)
        if data is None:
            return web.Response(status=404)
        sec = {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"}
        if request.query.get("do") != "pair":
            return web.Response(text=gapp.car_pair_page(), content_type="text/html", headers=sec)
        if request.method != "POST":
            return web.json_response({"ok": False}, status=405)
        fails = data.setdefault("car_fails", [])
        if gapp.car_locked(fails):
            return web.json_response(
                {"ok": False, "msg": "Za dużo błędnych prób – spróbuj za 10 minut"}, status=429
            )
        try:
            payload = await request.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "msg": "Nieprawidłowe żądanie"}, status=400)
        wid = gapp.redeem_car_code(data.setdefault("car_codes", {}), fails, payload.get("code"))
        user = next((u for u in data["users"] if u["webhook_id"] == wid), None) if wid else None
        if user is None:
            _LOGGER.warning("Gate Access: błędny kod parowania samochodu")
            return web.json_response({"ok": False, "msg": "Nieprawidłowy lub wygasły kod"}, status=403)
        if _activity(user) != "active":
            return web.json_response({"ok": False, "msg": "Ten dostęp nie jest aktywny"}, status=403)
        dev, err = gapp.register_device(user, payload.get("pub"), "Tesla · przeglądarka")
        if dev is None:
            return web.json_response({"ok": False, "msg": err}, status=400)
        dev["status"] = "approved"
        dev["paired"] = "car_code"
        _add_history(hass, entry, user["name"], wid, "dev_approved", None, "kod samochodu")
        await _save(hass, entry)
        return web.json_response(
            {"ok": True, "path": _app_path(wid), "dev": dev["id"], "name": user["name"]},
            headers=sec,
        )

    return _handler


@callback
def _register_car_webhook(hass: HomeAssistant, entry: ConfigEntry) -> None:
    try:
        webhook.async_register(
            hass, DOMAIN, "Brama – parowanie samochodu", CAR_WEBHOOK_ID,
            _make_car_handler(hass, entry), local_only=False, allowed_methods=["GET", "POST"],
        )
    except ValueError:
        _LOGGER.warning("Gate Access: webhook %s jest już zajęty – parowanie samochodu wyłączone",
                        CAR_WEBHOOK_ID)


@callback
def _register_webhook(hass: HomeAssistant, entry: ConfigEntry, user: dict) -> None:
    webhook.async_register(
        hass,
        DOMAIN,
        f"Brama {user['name']}",
        user["webhook_id"],
        _make_handler(hass, entry),
        local_only=False,
        allowed_methods=["GET", "POST"],
    )


@callback
def _unregister_webhook(hass: HomeAssistant, webhook_id: str) -> None:
    try:
        webhook.async_unregister(hass, webhook_id)
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
async def _save(hass: HomeAssistant, entry: ConfigEntry) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    await data["store"].async_save(
        {
            "users": data["users"],
            "history": data["history"],
            "stats": data["stats"],
            "vapid": data.get("vapid"),
        }
    )


def _resolve_target(entry: ConfigEntry, target: str | None) -> str | None:
    """Validate a requested target against the configured list."""
    targets = _targets(entry)
    if target is None:
        return targets[0] if targets else None
    if target not in targets:
        raise ValueError("Wybrany cel nie jest skonfigurowany")
    return target


async def async_create_user(
    hass: HomeAssistant,
    entry: ConfigEntry,
    name: str,
    *,
    starts_at: str | None = None,
    expires_at: str | None = None,
    uses_total: int | None = None,
    target: str | None = None,
) -> dict:
    users = hass.data[DOMAIN][entry.entry_id]["users"]
    if any(u["name"].casefold() == name.casefold() for u in users):
        raise ValueError(f"Dostęp dla „{name}” już istnieje")

    user = {
        "name": name.strip(),
        "webhook_id": _new_webhook_id(name),
        "created": dt_util.utcnow().isoformat(timespec="seconds"),
        "enabled": True,
        "starts_at": starts_at,
        "expires_at": expires_at,
        "uses_total": uses_total,
        "uses_left": uses_total,
        "target": _resolve_target(entry, target),
    }
    users.append(user)
    await _save(hass, entry)
    _register_webhook(hass, entry, user)
    return user


_UNSET = object()


async def async_update_user(
    hass: HomeAssistant,
    entry: ConfigEntry,
    webhook_id: str,
    *,
    enabled=_UNSET,
    target=_UNSET,
    ttl=_UNSET,
    app_targets=_UNSET,
    clear_push=False,
) -> dict:
    """Partial update: only the aspects provided are changed."""
    users = hass.data[DOMAIN][entry.entry_id]["users"]
    user = next((u for u in users if u["webhook_id"] == webhook_id), None)
    if user is None:
        raise KeyError(webhook_id)
    if enabled is not _UNSET:
        user["enabled"] = bool(enabled)
    if target is not _UNSET:
        user["target"] = _resolve_target(entry, target)
    if app_targets is not _UNSET:
        if not isinstance(app_targets, list):
            raise ValueError("app_targets musi być listą")
        user["app_targets"] = [_resolve_target(entry, t) for t in app_targets if t]
    if clear_push:
        user["push_subs"] = []
    if ttl is not _UNSET:
        starts_at, expires_at, uses_total = ttl
        user["starts_at"] = starts_at
        user["expires_at"] = expires_at
        user["uses_total"] = uses_total
        user["uses_left"] = uses_total
    await _save(hass, entry)
    return user


async def async_remove_user(
    hass: HomeAssistant, entry: ConfigEntry, webhook_id: str
) -> bool:
    users = hass.data[DOMAIN][entry.entry_id]["users"]
    before = len(users)
    users[:] = [u for u in users if u["webhook_id"] != webhook_id]
    if len(users) == before:
        return False
    _unregister_webhook(hass, webhook_id)
    await _save(hass, entry)
    return True


async def async_purge_expired(hass: HomeAssistant, entry: ConfigEntry) -> int:
    users = hass.data[DOMAIN][entry.entry_id]["users"]
    dead = [u for u in users if _is_expired(u)]
    for u in dead:
        _unregister_webhook(hass, u["webhook_id"])
    if dead:
        users[:] = [u for u in users if not _is_expired(u)]
        await _save(hass, entry)
    return len(dead)


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------
def _entry_for(hass: HomeAssistant) -> ConfigEntry | None:
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


def _user_public(user: dict) -> dict:
    out = {k: v for k, v in user.items() if k not in ("push_subs", "devices")}
    out["devices"] = [gapp.device_public(d) for d in user.get("devices", [])]
    out["push"] = [s.get("label", "urządzenie") for s in user.get("push_subs", [])]
    out["status"] = _status(user)
    return out


class GateUsersView(HomeAssistantView):
    url = "/api/gate_access/users"
    name = "api:gate_access:users"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = _entry_for(hass)
        if entry is None:
            return self.json({"users": []})
        users = hass.data[DOMAIN][entry.entry_id]["users"]
        return self.json({"users": [_user_public(u) for u in users]})

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = _entry_for(hass)
        if entry is None:
            return self.json_message("Integracja nieskonfigurowana", 400)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("Nieprawidłowe dane", 400)

        body = body or {}
        name = body.get("name", "")
        if not _valid_name(name):
            return self.json_message(
                "Podaj imię (litery, cyfry, spacja, kropka lub myślnik; do 40 znaków)",
                400,
            )
        try:
            starts_at, expires_at, uses_total = _parse_ttl(body)
            user = await async_create_user(
                hass,
                entry,
                name,
                starts_at=starts_at,
                expires_at=expires_at,
                uses_total=uses_total,
                target=body.get("target"),
            )
        except ValueError as err:
            code = 409 if "już istnieje" in str(err) else 400
            return self.json_message(str(err), code)
        return self.json(_user_public(user))


class GateUserView(HomeAssistantView):
    url = "/api/gate_access/users/{webhook_id}"
    name = "api:gate_access:user"
    requires_auth = True

    async def patch(self, request: web.Request, webhook_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = _entry_for(hass)
        if entry is None:
            return self.json_message("Integracja nieskonfigurowana", 400)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("Nieprawidłowe dane", 400)
        body = body or {}

        kwargs = {}
        if "enabled" in body:
            kwargs["enabled"] = bool(body["enabled"])
        if "target" in body:
            kwargs["target"] = body["target"]
        if "app_targets" in body:
            kwargs["app_targets"] = body["app_targets"]
        if body.get("clear_push"):
            kwargs["clear_push"] = True
        dact = body.get("device_action")
        if isinstance(dact, dict) and dact.get("op") in ("approve", "reject", "remove"):
            owner = await async_device_action(hass, entry, str(dact.get("id")), dact["op"])
            if owner is None or owner["webhook_id"] != webhook_id:
                return self.json_message("Nie znaleziono urządzenia", 404)
            return self.json(_user_public(owner))
        if body.get("set_ttl") or any(
            k in body for k in ("starts_at", "expires_at", "uses_total")
        ):
            try:
                kwargs["ttl"] = _parse_ttl(body)
            except ValueError as err:
                return self.json_message(str(err), 400)

        try:
            user = await async_update_user(hass, entry, webhook_id, **kwargs)
        except ValueError as err:
            return self.json_message(str(err), 400)
        except KeyError:
            return self.json_message("Nie znaleziono dostępu", 404)
        return self.json(_user_public(user))

    async def delete(self, request: web.Request, webhook_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = _entry_for(hass)
        if entry is None:
            return self.json_message("Integracja nieskonfigurowana", 400)
        removed = await async_remove_user(hass, entry, webhook_id)
        if not removed:
            return self.json_message("Nie znaleziono dostępu", 404)
        return self.json({"removed": webhook_id})


class GateTargetsView(HomeAssistantView):
    url = "/api/gate_access/targets"
    name = "api:gate_access:targets"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = _entry_for(hass)
        if entry is None:
            return self.json({"targets": []})
        out = [
            {"entity_id": eid, "name": _target_name(hass, eid)}
            for eid in _targets(entry)
        ]
        return self.json({"targets": out})


class GateOpenView(HomeAssistantView):
    """Open a target directly from the panel (admin action, no link)."""

    url = "/api/gate_access/open"
    name = "api:gate_access:open"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = _entry_for(hass)
        if entry is None:
            return self.json_message("Integracja nieskonfigurowana", 400)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("Nieprawidłowe dane", 400)
        try:
            target = _resolve_target(entry, (body or {}).get("target"))
        except ValueError as err:
            return self.json_message(str(err), 400)
        if target is None:
            return self.json_message("Brak skonfigurowanego obiektu", 400)
        user = request.get("hass_user")
        name = (user.name if user and user.name else None) or "Panel"
        tname = await _do_open(hass, entry, target, name, None, source="panel")
        return self.json({"opened": target, "name": tname})


class GateCloseView(HomeAssistantView):
    """Close a target directly from the panel (admin action)."""

    url = "/api/gate_access/close"
    name = "api:gate_access:close"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = _entry_for(hass)
        if entry is None:
            return self.json_message("Integracja nieskonfigurowana", 400)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("Nieprawidłowe dane", 400)
        try:
            target = _resolve_target(entry, (body or {}).get("target"))
        except ValueError as err:
            return self.json_message(str(err), 400)
        if target is None:
            return self.json_message("Brak skonfigurowanego obiektu", 400)
        user = request.get("hass_user")
        name = (user.name if user and user.name else None) or "Panel"
        tname = await _do_close(hass, entry, target, name, source="panel")
        return self.json({"closed": target, "name": tname})


class GateHistoryView(HomeAssistantView):
    url = "/api/gate_access/history"
    name = "api:gate_access:history"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = _entry_for(hass)
        if entry is None:
            return self.json({"history": []})
        return self.json({"history": hass.data[DOMAIN][entry.entry_id]["history"]})


class GateHistoryDeleteView(HomeAssistantView):
    """Delete a single history entry (by id) or clear all, guarded by password."""

    url = "/api/gate_access/history/delete"
    name = "api:gate_access:history_delete"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = _entry_for(hass)
        if entry is None:
            return self.json_message("Integracja nieskonfigurowana", 400)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        body = body or {}
        password = _cfg(entry, CONF_DELETE_PASSWORD, "")
        if password and body.get("password", "") != password:
            return self.json_message("Nieprawidłowe hasło", 403)
        removed = await _delete_history(hass, entry, body.get("id"))
        return self.json({"deleted": removed})


class GateStatsView(HomeAssistantView):
    """Per-entity open counts for today, this month, this year and total."""

    url = "/api/gate_access/stats"
    name = "api:gate_access:stats"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = _entry_for(hass)
        if entry is None:
            return self.json({"enabled": False, "stats": []})
        now = dt_util.now()
        dk = now.strftime("%Y-%m-%d")
        mk = now.strftime("%Y-%m")
        yk = now.strftime("%Y")
        stats = hass.data[DOMAIN][entry.entry_id]["stats"]
        out = []
        for eid in _targets(entry):
            e = stats.get(eid, {})
            out.append(
                {
                    "entity_id": eid,
                    "name": _target_name(hass, eid),
                    "today": e.get("day", {}).get(dk, 0),
                    "month": e.get("month", {}).get(mk, 0),
                    "year": e.get("year", {}).get(yk, 0),
                    "total": e.get("total", 0),
                }
            )
        return self.json(
            {"enabled": bool(_cfg(entry, CONF_STATS, False)), "stats": out}
        )


class GateSettingsView(HomeAssistantView):
    """Read-only settings summary for the panel's settings tab."""

    url = "/api/gate_access/settings"
    name = "api:gate_access:settings"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = _entry_for(hass)
        if entry is None:
            return self.json({})
        return self.json(
            {
                "targets": [
                    {
                        "entity_id": eid,
                        "name": _target_name(hass, eid),
                        "close_after": _close_secs(entry, eid),
                        "state_entity": gapp.state_entity(entry, eid),
                        "state": gapp.is_open(hass, entry, eid),
                        "remind_min": gapp.remind_minutes(hass, entry, eid),
                        "can_close": eid.split(".")[0] in CLOSE_ACTIONS,
                    }
                    for eid in _targets(entry)
                ],
                "show_close": bool(_cfg(entry, CONF_SHOW_CLOSE, DEFAULT_SHOW_CLOSE)),
                "confirm_open": bool(_cfg(entry, CONF_CONFIRM_OPEN, DEFAULT_CONFIRM_OPEN)),
                "base_url": (_cfg(entry, CONF_BASE_URL, "") or "").rstrip("/"),
                "log_path": _cfg(entry, CONF_LOG_PATH, DEFAULT_LOG_PATH),
                "admin_only": bool(_cfg(entry, CONF_ADMIN_ONLY, DEFAULT_ADMIN_ONLY)),
                "stats": bool(_cfg(entry, CONF_STATS, False)),
                "log_closings": bool(_cfg(entry, CONF_LOG_CLOSINGS, False)),
                "has_delete_password": bool(_cfg(entry, CONF_DELETE_PASSWORD, "")),
                "approve_notify": _cfg(entry, CONF_APPROVE_NOTIFY, "") or "",
                "entry_id": entry.entry_id,
            }
        )


class GatePurgeView(HomeAssistantView):
    url = "/api/gate_access/purge"
    name = "api:gate_access:purge"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = _entry_for(hass)
        if entry is None:
            return self.json_message("Integracja nieskonfigurowana", 400)
        count = await async_purge_expired(hass, entry)
        return self.json({"purged": count})


class GatePushTestView(HomeAssistantView):
    """Send a test push to one person's devices (admin)."""

    url = "/api/gate_access/push_test"
    name = "api:gate_access:push_test"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = _entry_for(hass)
        if entry is None:
            return self.json_message("Integracja nieskonfigurowana", 400)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        wid = (body or {}).get("webhook_id")
        users = hass.data[DOMAIN][entry.entry_id]["users"]
        user = next((u for u in users if u["webhook_id"] == wid), None)
        if user is None:
            return self.json_message("Nie znaleziono dostępu", 404)
        if not user.get("push_subs"):
            return self.json_message("Osoba nie włączyła przypomnień w aplikacji", 400)
        sent = await gapp.send_push(
            hass, entry, user, "Brama – test", "Powiadomienia działają.",
            f"{_app_path(wid)}?app=1", "test",
        )
        await _save(hass, entry)
        return self.json({"sent": sent, "devices": len(user.get("push_subs", []))})


class GateStaticView(HomeAssistantView):
    """Serve panel.js / card.js with a forced JavaScript MIME type (UTF-8)."""

    url = STATIC_URL + "/{filename}"
    name = "gate_access:static"
    requires_auth = False

    async def get(self, request: web.Request, filename: str) -> web.Response:
        if filename not in _ASSETS:
            return web.Response(status=404)
        hass: HomeAssistant = request.app["hass"]
        path = os.path.join(os.path.dirname(__file__), "frontend", filename)

        def _read() -> str:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()

        body = await hass.async_add_executor_job(_read)
        return web.Response(
            body=body.encode("utf-8"),
            content_type="application/javascript",
            charset="utf-8",
            headers={"Cache-Control": "no-cache"},
        )


async def _person_or_user(hass: HomeAssistant, user_id: str) -> str:
    """Human-readable name for a HA user_id (prefer a linked person entity)."""
    for st in hass.states.async_all("person"):
        if st.attributes.get("user_id") == user_id:
            return st.attributes.get("friendly_name") or st.name
    user = await hass.auth.async_get_user(user_id)
    if user and user.name:
        return user.name
    return "użytkownik HA"


def _became_open(old, new, domain: str) -> bool:
    if new is None:
        return False
    ns = new.state
    prev = old.state if old else None
    if domain == "cover":
        return ns in ("open", "opening") and prev not in ("open", "opening")
    if domain == "lock":
        return ns == "unlocked" and prev != "unlocked"
    if domain in ("switch", "input_boolean", "light", "script"):
        return ns == "on" and prev != "on"
    if domain in ("button", "input_button"):
        return prev is not None and ns != prev
    return False


def _became_closed(old, new, domain: str) -> bool:
    if new is None or old is None:
        return False
    ns = new.state
    prev = old.state
    if domain == "cover":
        return ns in ("closed", "closing") and prev not in ("closed", "closing")
    if domain == "lock":
        return ns == "locked" and prev != "locked"
    if domain in ("switch", "input_boolean", "light", "script"):
        return ns == "off" and prev != "off"
    return False


@callback
def _register_state_tracking(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Log HA-initiated opens (by user) and, optionally, closings."""
    targets = _targets(entry)
    if not targets:
        return

    @callback
    def _changed(event):
        entity_id = event.data["entity_id"]
        domain = entity_id.split(".")[0]
        old = event.data.get("old_state")
        new = event.data.get("new_state")
        ctx = event.context
        user_id = ctx.user_id if ctx else None

        if _became_open(old, new, domain):
            _schedule_auto_close(hass, entry, entity_id, domain)
            if _is_own(hass, entry, ctx):
                return  # our webhook/panel open — already recorded by _do_open
            hass.async_create_task(
                _log_state(hass, entry, entity_id, "opened", user_id)
            )
            return

        if bool(_cfg(entry, CONF_LOG_CLOSINGS, False)) and _became_closed(
            old, new, domain
        ):
            if _is_own(hass, entry, ctx):
                return  # our manual close via link — already recorded by _do_close
            hass.async_create_task(
                _log_state(hass, entry, entity_id, "closed", user_id)
            )

    unsub = async_track_state_change_event(hass, targets, _changed)
    hass.data[DOMAIN][entry.entry_id]["unsub_state"] = unsub

    # Reminders: stop as soon as the state sensor reports the opening closed.
    watch = {gapp.state_entity(entry, t): t for t in targets}

    @callback
    def _state_changed(event):
        tgt = watch.get(event.data["entity_id"])
        if tgt and gapp.is_open(hass, entry, tgt) is False:
            gapp.cancel_reminder(hass, entry, tgt)

    hass.data[DOMAIN][entry.entry_id]["unsub_remind"] = async_track_state_change_event(
        hass, list(watch), _state_changed
    )


async def _log_state(
    hass: HomeAssistant, entry: ConfigEntry, entity_id: str, status: str, user_id
) -> None:
    if user_id:
        name = await _person_or_user(hass, user_id)
        source = "ha"
    elif status == "opened":
        name = "Pilot"
        source = "pilot"
    else:
        name = "automat"
        source = "auto"
    await _record(hass, entry, name, entity_id, source, status)


# ---------------------------------------------------------------------------
# Global registration (views + assets + service, once) and panel (per setup)
# ---------------------------------------------------------------------------
async def _async_register_global(hass: HomeAssistant) -> None:
    store = hass.data[DOMAIN]
    if store.get("_global"):
        return
    store["_global"] = True

    hass.http.register_view(GateUsersView())
    hass.http.register_view(GateUserView())
    hass.http.register_view(GateTargetsView())
    hass.http.register_view(GateOpenView())
    hass.http.register_view(GateCloseView())
    hass.http.register_view(GateHistoryView())
    hass.http.register_view(GateHistoryDeleteView())
    hass.http.register_view(GateStatsView())
    hass.http.register_view(GateSettingsView())
    hass.http.register_view(GatePurgeView())
    hass.http.register_view(GateStaticView())
    hass.http.register_view(GatePushTestView())

    add_extra_js_url(hass, _asset_url("card.js"))

    async def _handle_log_open(call: ServiceCall) -> None:
        entry = _entry_for(hass)
        if entry is None:
            return
        target = call.data.get("target") or _default_target(entry)
        source = call.data.get("source") or "usługa"
        await _record_open(hass, entry, call.data["name"], target, source)

    hass.services.async_register(
        DOMAIN, SERVICE_LOG_OPEN, _handle_log_open, schema=LOG_OPEN_SCHEMA
    )


async def _register_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """(Re)register the sidebar panel; require_admin depends on the sharing option."""
    async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
    admin_only = bool(_cfg(entry, CONF_ADMIN_ONLY, DEFAULT_ADMIN_ONLY))
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name="gate-access-panel",
        module_url=_asset_url("panel.js"),
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        require_admin=admin_only,
        embed_iframe=False,
    )


# ---------------------------------------------------------------------------
# Config entry lifecycle
# ---------------------------------------------------------------------------
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    stored = await store.async_load() or {}

    history = stored.get("history", [])
    for item in history:  # migrate legacy entries so they can be deleted singly
        if "id" not in item:
            item["id"] = secrets.token_hex(6)

    hass.data[DOMAIN][entry.entry_id] = {
        "store": store,
        "users": stored.get("users", []),
        "history": history,
        "stats": stored.get("stats", {}),
        "vapid": stored.get("vapid") or gapp.new_vapid(),
        "reminders": {},
    }
    hass.data[DOMAIN][entry.entry_id]["save"] = lambda: _save(hass, entry)

    async def _on_notify_action(event) -> None:
        action = str(event.data.get("action", ""))
        if not action.startswith(APPROVE_ACTION_PREFIX):
            return
        rest = action[len(APPROVE_ACTION_PREFIX):]
        op, _, dev_id = rest.partition("_")
        if op in ("APPROVE", "REJECT") and dev_id:
            await async_device_action(hass, entry, dev_id, op.lower())

    entry.async_on_unload(
        hass.bus.async_listen("mobile_app_notification_action", _on_notify_action)
    )
    if not stored.get("vapid"):
        await _save(hass, entry)

    for user in hass.data[DOMAIN][entry.entry_id]["users"]:
        _register_webhook(hass, entry, user)
    _register_car_webhook(hass, entry)

    await _async_register_global(hass)
    await _register_panel(hass, entry)
    _register_state_tracking(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_update_listener))
    return True


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data:
        for key in ("unsub_state", "unsub_remind"):
            unsub = data.get(key)
            if unsub:
                unsub()
        for item in data.get("reminders", {}).values():
            if item.get("cancel"):
                item["cancel"]()
        for user in data["users"]:
            _unregister_webhook(hass, user["webhook_id"])
    _unregister_webhook(hass, CAR_WEBHOOK_ID)
    return True
