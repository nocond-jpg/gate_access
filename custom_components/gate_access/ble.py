"""Bluetooth (BLE) emergency control – key sync to ESPHome gate controllers (2.0).

The ESP never receives anything that can open the gate by itself: only the PUBLIC
P-256 keys of approved app devices (the private keys stay non-extractable in the
phones). Each command sent over BLE is signed on the phone over a one-time nonce
issued by the ESP, which verifies it locally – so it works without network and HA.

Who gets a key for a target:
  * the person is enabled and active (or not yet started – the ESP enforces the window),
  * the link has no use counter (counters cannot be enforced offline),
  * the target is one of the person's app targets,
  * the device is approved.
Time windows (starts_at / expires_at) are passed to the ESP; a time-limited key is
refused there when the ESP has no valid clock. Removing a person/device or expiry
re-syncs the list (debounced after every change and checked every 5 minutes).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from typing import Any, Callable

from cryptography.hazmat.primitives import serialization

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

MAX_KEYS = 16          # must match brama_ble.h
FORCE_RESEND = 6 * 3600


def service_name(value: Any) -> str | None:
    """Normalise 'esphome.brama_ble_klucze' / 'brama_ble_klucze' to the bare action name."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if v.startswith("esphome."):
        v = v[len("esphome."):]
    if not v or not all(c.isalnum() or c == "_" for c in v):
        return None
    return v


def point_b64(spki_b64: str) -> str | None:
    """SPKI (base64 DER, P-256) -> base64 of the 65-byte uncompressed point."""
    from .app import _load_pub

    try:
        key = _load_pub(spki_b64)
    except (ValueError, TypeError):
        return None
    raw = key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.b64encode(raw).decode()


def _ts(value: Any) -> int:
    if not value:
        return 0
    dt = dt_util.parse_datetime(value) if isinstance(value, str) else None
    return int(dt.timestamp()) if dt else 0


def payload_for(
    target: str,
    users: list[dict],
    user_targets: Callable[[dict], list[str]],
    activity: Callable[[dict], str],
) -> str:
    """Key list for one target in the brama_ble format: dev,b64,from,until;..."""
    rows: list[tuple[int, str, str]] = []
    for user in users:
        if activity(user) not in ("active", "pending"):
            continue
        if user.get("uses_total") is not None:
            continue
        if target not in user_targets(user):
            continue
        frm, until = _ts(user.get("starts_at")), _ts(user.get("expires_at"))
        for dev in user.get("devices", []):
            if dev.get("status") != "approved":
                continue
            dev_id = dev.get("id")
            if not isinstance(dev_id, str) or len(dev_id) != 16:
                continue
            pt = point_b64(dev.get("pub", ""))
            if not pt:
                continue
            limited = 1 if (frm or until) else 0
            rows.append((limited, dev_id, f"{dev_id},{pt},{frm},{until}"))
    # owners / unlimited keys first, then a stable order by device id
    rows.sort(key=lambda r: (r[0], r[1]))
    if len(rows) > MAX_KEYS:
        _LOGGER.warning(
            "Gate Access BLE: %s ma %d kluczy, ESP przyjmie tylko %d", target, len(rows), MAX_KEYS
        )
    return ";".join(r[2] for r in rows[:MAX_KEYS])


def version_of(payload: str) -> str:
    """Same as brama_ble::version(): first 16 hex chars of SHA-256 of the payload."""
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


async def async_sync(
    hass: HomeAssistant,
    ble_map: dict[str, str],
    users: list[dict],
    user_targets: Callable[[dict], list[str]],
    activity: Callable[[dict], str],
    sent: dict[str, tuple[str, float]],
    force: bool = False,
) -> None:
    """Push key lists that changed (or are due for a periodic resend) to the ESPs."""
    now = time.monotonic()
    for target, raw in ble_map.items():
        svc = service_name(raw)
        if not svc:
            continue
        payload = payload_for(target, users, user_targets, activity)
        ver = version_of(payload)
        last = sent.get(target)
        if not force and last and last[0] == ver and now - last[1] < FORCE_RESEND:
            continue
        if not hass.services.has_service("esphome", svc):
            _LOGGER.debug("Gate Access BLE: usługa esphome.%s niedostępna", svc)
            continue
        try:
            await hass.services.async_call(
                "esphome", svc, {"klucze": payload}, blocking=True
            )
        except Exception as err:  # noqa: BLE001 – ESP offline etc.; retried later
            _LOGGER.warning("Gate Access BLE: nie wysłano kluczy do %s: %s", svc, err)
            continue
        sent[target] = (ver, now)
        _LOGGER.info(
            "Gate Access BLE: wysłano %d kluczy do esphome.%s (wersja %s)",
            payload.count(";") + 1 if payload else 0, svc, ver,
        )
