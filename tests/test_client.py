"""Tests for the protocol-only client helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

CLIENT_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "huawei_hilink_opple"
    / "client.py"
)
SPEC = importlib.util.spec_from_file_location("hilink_client_under_test", CLIENT_PATH)
assert SPEC is not None and SPEC.loader is not None
CLIENT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CLIENT
SPEC.loader.exec_module(CLIENT)


def test_client_request_timeout_can_be_shortened_for_recovery() -> None:
    """Recovery reads can use a short timeout without changing the normal default."""
    normal = CLIENT.HiLinkLegacyClient("192.0.2.1", "device", "00")
    recovery = CLIENT.HiLinkLegacyClient(
        "192.0.2.1", "device", "00", timeout=1.0
    )

    assert normal.timeout == CLIENT.DEFAULT_REQUEST_TIMEOUT == 3.5
    assert recovery.timeout == 1.0


def test_coap_round_trip_with_extended_options() -> None:
    """High-numbered vendor options survive CoAP encoding and parsing."""
    token = b"abc"
    options = [
        (CLIENT.OPT_URI_PATH, b"devDataInfo"),
        (CLIENT.OPT_CONTENT_FORMAT, CLIENT._uint_bytes(50)),
        (CLIENT.OPT_SESSION_ID, b"session"),
        (CLIENT.OPT_DEVICE_ID, b"synthetic-device"),
        (CLIENT.OPT_SEQUENCE, CLIENT._uint_bytes(17)),
    ]
    packet = CLIENT._build_coap(
        code=2,
        message_id=42,
        token=token,
        options=options,
        payload=b'{}',
    )

    parsed = CLIENT._parse_coap(packet)

    assert parsed.code == 2
    assert parsed.token == token
    assert parsed.options == tuple(options)
    assert parsed.payload == b'{}'


def test_parse_light_state_from_service_list() -> None:
    """The state parser normalizes the three HA-facing services."""
    response = {
        "services": [
            {"sid": "switch", "data": {"on": 1}},
            {"sid": "brightness", "data": {"brightness": 37}},
            {"sid": "cct", "data": {"colorTemperature": 4200}},
        ]
    }

    state = CLIENT.parse_light_state(response)

    assert state == CLIENT.LightState(True, 37, 4200)


def test_parse_light_state_from_stringified_json() -> None:
    """Some firmware nests the service list in a JSON string."""
    response = {
        "payload": (
            '[{"sid":"switch","data":{"on":"false"}},'
            '{"sid":"brightness","data":{"brightness":1}},'
            '{"sid":"cct","data":{"colorTemperature":2700}}]'
        )
    }

    state = CLIENT.parse_light_state(response)

    assert state == CLIENT.LightState(False, 1, 2700)


def test_parse_light_state_rejects_missing_fields() -> None:
    """An incomplete response must not become an optimistic HA state."""
    with pytest.raises(CLIENT.HiLinkError):
        CLIENT.parse_light_state({"services": []})


def test_aes_round_trip() -> None:
    """AES-CBC helpers preserve arbitrary JSON-sized plaintext."""
    key = bytes(range(16))
    iv = bytes(reversed(range(16)))
    plaintext = b'{"brightness":53}'

    encrypted = CLIENT._aes_encrypt(plaintext, key, iv)

    assert encrypted != plaintext
    assert CLIENT._aes_decrypt(encrypted, key, iv) == plaintext
