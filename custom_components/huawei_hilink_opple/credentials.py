"""Parse and temporarily cache local-control credentials."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_AUTH_CODE, CONF_DEVICE_ID, CONF_MODEL, DEFAULT_MODEL, DOMAIN

CACHE_KEY = "credential_cache"
CACHE_TIMER_KEY = "credential_cache_timer"
CREDENTIAL_CACHE_SECONDS = 600
_AUTH_CODE = re.compile(r"^[0-9a-fA-F]{32,256}$")


def parse_credential_json(raw: str) -> list[dict[str, str]]:
    """Parse clipboard JSON and retain only fields needed for local matching."""
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON") from exc
    records = payload.get("devices") if isinstance(payload, dict) else payload
    if not isinstance(records, list) or not records or len(records) > 200:
        raise ValueError("devices must be a non-empty list")

    normalized: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("invalid device record")
        device_id = str(item.get(CONF_DEVICE_ID, "")).strip()
        auth_code = str(item.get(CONF_AUTH_CODE, "")).strip()
        if not device_id or not _AUTH_CODE.fullmatch(auth_code):
            raise ValueError("invalid device credentials")
        if device_id in seen_ids:
            continue
        seen_ids.add(device_id)
        record = {
            CONF_DEVICE_ID: device_id,
            CONF_AUTH_CODE: auth_code,
            CONF_MODEL: str(item.get(CONF_MODEL) or DEFAULT_MODEL).strip(),
        }
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            record["name"] = name.strip()
        normalized.append(record)
    return normalized


def clear_cached_credentials(hass: HomeAssistant) -> None:
    """Remove cached credentials and cancel their expiry callback."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    timer = domain_data.pop(CACHE_TIMER_KEY, None)
    if timer is not None:
        timer.cancel()
    domain_data.pop(CACHE_KEY, None)


def cache_credentials(hass: HomeAssistant, records: list[dict[str, str]]) -> None:
    """Keep credential records in HA memory for ten minutes."""
    clear_cached_credentials(hass)
    domain_data = hass.data.setdefault(DOMAIN, {})
    cache = {
        "expires_at": time.monotonic() + CREDENTIAL_CACHE_SECONDS,
        "records": [dict(record) for record in records],
    }
    domain_data[CACHE_KEY] = cache

    def expire() -> None:
        current = hass.data.get(DOMAIN, {}).get(CACHE_KEY)
        if current is cache:
            hass.data[DOMAIN].pop(CACHE_KEY, None)
            hass.data[DOMAIN].pop(CACHE_TIMER_KEY, None)

    domain_data[CACHE_TIMER_KEY] = hass.loop.call_later(CREDENTIAL_CACHE_SECONDS, expire)


def get_cached_credentials(hass: HomeAssistant) -> list[dict[str, str]]:
    """Return a copy of unexpired records from HA memory."""
    cache: Any = hass.data.get(DOMAIN, {}).get(CACHE_KEY)
    if not isinstance(cache, dict) or time.monotonic() >= cache.get("expires_at", 0):
        clear_cached_credentials(hass)
        return []
    records = cache.get("records")
    if not isinstance(records, list):
        clear_cached_credentials(hass)
        return []
    return [dict(record) for record in records]
