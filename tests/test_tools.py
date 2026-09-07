from __future__ import annotations

import json
import os
import urllib.parse

import pytest

from tools.credential_web import PAGE, CredentialState
from tools.fetch_huawei_credentials import (
    SCOPES,
    authorization_code_from_input,
    build_authorization_url,
    collect_authcodes,
    collect_device_records,
    write_secret_file,
)
from tools.match_hilink_credentials import load_credentials


def test_authorization_url_and_callback_state() -> None:
    url, state = build_authorization_url()
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.hostname is not None and parsed.hostname.endswith("huawei.com")
    assert query["state"] == [state]
    assert query["scope"] == [" ".join(SCOPES)]
    assert query["redirect_uri"] == ["hms://redirect_url"]

    callback = f"hms://redirect_url?code=one-time-code&state={urllib.parse.quote(state)}"
    assert authorization_code_from_input(callback, state) == "one-time-code"
    with pytest.raises(ValueError, match="state"):
        authorization_code_from_input(callback, "different-state")


def test_collect_nested_device_and_authcode_shapes() -> None:
    auth_code = "ab" * 16
    device_response = {
        "payload": json.dumps(
            {
                "items": [
                    {
                        "devId": "device-alpha",
                        "productId": "2BB0",
                        "model": "MX420-D24-WTT",
                        "deviceName": "Test light",
                    }
                ]
            }
        )
    }
    assert collect_device_records(device_response) == [
        {
            "device_id": "device-alpha",
            "product_id": "2BB0",
            "model": "MX420-D24-WTT",
            "name": "Test light",
        }
    ]
    auth_response = {"data": [{"deviceId": "device-alpha", "authCode": auth_code}]}
    assert collect_authcodes(auth_response, []) == [
        {"device_id": "device-alpha", "auth_code": auth_code}
    ]


def test_secret_file_permissions_and_loader(tmp_path) -> None:
    path = tmp_path / "private.json"
    payload = {
        "devices": [{"device_id": "device-alpha", "auth_code": "cd" * 16}],
    }
    write_secret_file(path, payload)

    assert os.stat(path).st_mode & 0o777 == 0o600
    assert load_credentials(path) == payload["devices"]
    with pytest.raises(FileExistsError):
        write_secret_file(path, payload)


def test_clear_invalidates_in_flight_web_result() -> None:
    state = CredentialState()
    generation = state.begin("fetching", "fetching", [])
    state.clear()

    accepted = state.update_if_current(
        generation,
        "ready",
        "ready",
        [{"device_id": "device-alpha", "auth_code": "ef" * 16}],
    )

    assert accepted is False
    assert state.snapshot()["devices"] == []


def test_web_helper_only_exports_credentials() -> None:
    assert "复制 JSON" in PAGE
    assert "api/match" not in PAGE
    assert "只读匹配 IP" not in PAGE
