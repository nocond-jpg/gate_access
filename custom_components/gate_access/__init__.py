"""Gate Access (PoC) — per-person webhooks for multiple openings.

Supports several targets (gate, wicket, ...). A target can be a cover, lock,
switch, button or script; the open/close action is chosen from the entity's
domain. Each link is bound to one target. Links can be permanent, time-limited,
use-limited or one-time, editable after creation, and every open (or blocked
attempt) is recorded in a per-link history. An admin panel and a Lovelace card
create and manage links.
"""

from __future__ import annotations

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
from homeassistant.core import HomeAssistant, ServiceCall, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADMIN_ONLY,
    CONF_CLOSE_AFTER,
    CONF_DELETE_PASSWORD,
    CONF_GATE_ENTITY,
    CONF_LOG_CLOSINGS,
    CONF_LOG_PATH,
    CONF_RATE_LIMIT,
    CONF_STATS,
    CONF_TARGETS,
    DEFAULT_ADMIN_ONLY,
    DEFAULT_CLOSE_AFTER,
    DEFAULT_LOG_PATH,
    DEFAULT_RATE_LIMIT,
    DOMAIN,
    EVENT_OPENED,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL_PATH,
    STATIC_URL,
    STORAGE_KEY,
    STORAGE_VERSION,
)

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


def _html(name: str, sub: str, dim: bool = False) -> str:
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
        "<title>Brama</title><style>"
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


async def _do_open(
    hass: HomeAssistant,
    entry: ConfigEntry,
    target: str | None,
    name: str,
    webhook_id: str | None,
    source: str = "link",
) -> str:
    """Actuate a target (any supported domain), auto-close, then record it."""
    domain = target.split(".")[0] if target else ""
    opener = OPEN_ACTIONS.get(domain)
    if target and opener:
        await hass.services.async_call(
            opener[0], opener[1], {"entity_id": target}, blocking=False
        )
        try:
            close_after = int(_cfg(entry, CONF_CLOSE_AFTER, DEFAULT_CLOSE_AFTER))
        except (TypeError, ValueError):
            close_after = 0
        closer = CLOSE_ACTIONS.get(domain)
        if close_after > 0 and closer:
            async def _auto_close(_now, _svc=closer, _eid=target):
                await hass.services.async_call(
                    _svc[0], _svc[1], {"entity_id": _eid}, blocking=False
                )

            async_call_later(hass, close_after, _auto_close)

    return await _record_open(hass, entry, name, target, source, webhook_id)


# ---------------------------------------------------------------------------
# Webhook handling
# ---------------------------------------------------------------------------
def _make_handler(hass: HomeAssistant, entry: ConfigEntry):
    async def _handler(hass_: HomeAssistant, webhook_id: str, request: web.Request):
        data = hass.data[DOMAIN][entry.entry_id]
        user = next(
            (u for u in data["users"] if u["webhook_id"] == webhook_id), None
        )
        name = user["name"] if user else "nieznany"
        target = (user.get("target") if user else None) or _default_target(entry)

        act = _activity(user) if user else "active"
        if user and act != "active":
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
            return web.Response(
                text=_html(head, sub, dim=True), content_type="text/html", status=403
            )

        if user and not _rate_ok(hass, entry, webhook_id):
            return web.Response(
                text=_html("Za dużo prób", "Spróbuj ponownie za chwilę.", dim=True),
                content_type="text/html",
                status=429,
            )

        if user and user.get("uses_left") is not None:
            user["uses_left"] = max(0, user["uses_left"] - 1)

        tname = await _do_open(hass, entry, target, name, webhook_id)
        return web.Response(
            text=_html(f"Otwarto: {tname}", f"{name} · {datetime.now():%H:%M}"),
            content_type="text/html",
        )

    return _handler


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
        {"users": data["users"], "history": data["history"], "stats": data["stats"]}
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
    return {**user, "status": _status(user)}


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
                    {"entity_id": eid, "name": _target_name(hass, eid)}
                    for eid in _targets(entry)
                ],
                "close_after": int(_cfg(entry, CONF_CLOSE_AFTER, DEFAULT_CLOSE_AFTER) or 0),
                "log_path": _cfg(entry, CONF_LOG_PATH, DEFAULT_LOG_PATH),
                "admin_only": bool(_cfg(entry, CONF_ADMIN_ONLY, DEFAULT_ADMIN_ONLY)),
                "stats": bool(_cfg(entry, CONF_STATS, False)),
                "log_closings": bool(_cfg(entry, CONF_LOG_CLOSINGS, False)),
                "has_delete_password": bool(_cfg(entry, CONF_DELETE_PASSWORD, "")),
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
        user_id = event.context.user_id if event.context else None

        if _became_open(old, new, domain):
            if not user_id:
                return  # automation / our own open — recorded by its own path
            hass.async_create_task(
                _log_state(hass, entry, entity_id, "opened", user_id)
            )
            return

        if bool(_cfg(entry, CONF_LOG_CLOSINGS, False)) and _became_closed(
            old, new, domain
        ):
            hass.async_create_task(
                _log_state(hass, entry, entity_id, "closed", user_id)
            )

    unsub = async_track_state_change_event(hass, targets, _changed)
    hass.data[DOMAIN][entry.entry_id]["unsub_state"] = unsub


async def _log_state(
    hass: HomeAssistant, entry: ConfigEntry, entity_id: str, status: str, user_id
) -> None:
    if user_id:
        name = await _person_or_user(hass, user_id)
        source = "ha"
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
    hass.http.register_view(GateHistoryView())
    hass.http.register_view(GateHistoryDeleteView())
    hass.http.register_view(GateStatsView())
    hass.http.register_view(GateSettingsView())
    hass.http.register_view(GatePurgeView())
    hass.http.register_view(GateStaticView())

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
    }

    for user in hass.data[DOMAIN][entry.entry_id]["users"]:
        _register_webhook(hass, entry, user)

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
        unsub = data.get("unsub_state")
        if unsub:
            unsub()
        for user in data["users"]:
            _unregister_webhook(hass, user["webhook_id"])
    return True
