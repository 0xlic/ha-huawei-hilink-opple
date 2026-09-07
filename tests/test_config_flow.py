from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.huawei_hilink_opple import config_flow
from custom_components.huawei_hilink_opple.const import DOMAIN
from custom_components.huawei_hilink_opple.credentials import (
    CACHE_KEY,
    cache_credentials,
    clear_cached_credentials,
    get_cached_credentials,
    parse_credential_json,
)


class FakeConfigEntries:
    def __init__(self, entries=None):
        self.entries = entries or []

    def async_entries(self, domain, include_ignore=False):
        assert domain == DOMAIN
        return self.entries


class FakeHass:
    def __init__(self, loop, entries=None):
        self.data = {}
        self.loop = loop
        self.config_entries = FakeConfigEntries(entries)


def make_flow(hass):
    flow = config_flow.HuaweiHiLinkOppleConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.context = {"source": "user"}
    flow.flow_id = "test-flow"
    flow.async_set_unique_id = AsyncMock(return_value=None)
    flow._abort_if_unique_id_configured = lambda **kwargs: None
    return flow


def records_json():
    return json.dumps(
        {
            "access_token": "must-be-ignored",
            "devices": [
                {
                    "device_id": "already-added",
                    "auth_code": "11" * 16,
                    "model": "MX420-D24-WTT",
                    "name": "云端名称一",
                    "host": "192.0.2.1",
                },
                {
                    "device_id": "wrong-device",
                    "auth_code": "33" * 16,
                    "model": "MX420-D24-WTT",
                    "name": "云端名称三",
                },
                {
                    "device_id": "new-device",
                    "auth_code": "22" * 16,
                    "model": "MX480-D48-WTT",
                    "name": "云端名称二",
                },
            ],
        }
    )


def test_parser_whitelists_fields_and_rejects_invalid_json():
    records = parse_credential_json(records_json())
    assert records[0] == {
        "device_id": "already-added",
        "auth_code": "11" * 16,
        "model": "MX420-D24-WTT",
        "name": "云端名称一",
    }
    assert all("access_token" not in record and "host" not in record for record in records)
    with pytest.raises(ValueError):
        parse_credential_json('{"devices": [{"device_id": "x", "auth_code": "bad"}]}')


def test_import_matches_only_unused_device_and_reuses_cache(monkeypatch):
    async def scenario():
        entry = SimpleNamespace(unique_id="already-added")
        hass = FakeHass(asyncio.get_running_loop(), [entry])
        validate = AsyncMock(side_effect=[config_flow.HiLinkError("wrong credential"), None])
        monkeypatch.setattr(config_flow, "validate_input", validate)

        first = make_flow(hass)
        initial = await first.async_step_user()
        assert initial["step_id"] == "credentials"
        imported = await first.async_step_credentials(
            {config_flow.CONF_CREDENTIALS_JSON: records_json()}
        )
        assert imported["step_id"] == "local"
        assert CACHE_KEY in hass.data[DOMAIN]

        created = await first.async_step_local(
            {"name": "书房灯", "host": "192.168.31.37", "clear_cached_credentials": False}
        )
        assert created["type"].value == "create_entry"
        assert created["data"]["device_id"] == "new-device"
        assert created["data"]["host"] == "192.168.31.37"
        assert validate.await_count == 2
        assert validate.await_args.args[1]["device_id"] == "new-device"

        second = make_flow(hass)
        reused = await second.async_step_user()
        assert reused["step_id"] == "local"
        clear_result = await second.async_step_local(
            {"name": "", "host": "", "clear_cached_credentials": True}
        )
        assert clear_result["step_id"] == "credentials"
        assert get_cached_credentials(hass) == []

    asyncio.run(scenario())


def test_cache_expiry_callback_erases_records():
    async def scenario():
        hass = FakeHass(asyncio.get_running_loop())
        cache_credentials(
            hass, [{"device_id": "device", "auth_code": "aa" * 16, "model": "model"}]
        )
        cache = hass.data[DOMAIN][CACHE_KEY]
        # Exercise the exact callback registered with the event loop without waiting ten minutes.
        timer = hass.data[DOMAIN]["credential_cache_timer"]
        timer._callback()
        assert CACHE_KEY not in hass.data[DOMAIN]
        clear_cached_credentials(hass)
        assert cache["records"][0]["device_id"] == "device"

    asyncio.run(scenario())
