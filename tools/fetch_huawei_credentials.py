#!/usr/bin/env python3
"""Fetch local HiLink credentials after an interactive Huawei authorization.

This helper intentionally never prints or persists OAuth access/refresh tokens.
The output contains local device credentials and is created with mode 0600.

The compatibility profile reproduces a flow observed in Huawei Smart Life
17.0.3.320. It is not a documented or supported third-party API and can stop
working when Huawei changes the application or service.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import secrets
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BASE_URL = "https://smarthome.hicloud.com"
AUTHORIZE_URL = "https://oauth-login1.cloud.huawei.com/oauth2/v3/authorize"
APP_ID = "10406921"
APP_VERSION = "17.0.3.320"
REDIRECT_URI = "hms://redirect_url"
TOKEN_URL = f"{BASE_URL}/smart-life/v2/hms-lite/token"
HOMES_URL = f"{BASE_URL}/userApp/v1.2.0/homes"
DEVICES_V2_URL = f"{BASE_URL}/smart-life/v2/devices"
DEVICES_V3_URL = f"{BASE_URL}/smart-life/v3/devices"
AUTHCODE_URL = f"{BASE_URL}/home-manager/v1/homes/devices/authcode"
SCOPES = (
    "openid",
    "https://www.huawei.com/auth/account/base.profile",
    "https://smarthome.com/auth/smarthome/devices",
    "https://smarthome.com/auth/smarthome/skill",
)
AUTH_RE = re.compile(r"^[0-9a-fA-F]{32,256}$")
TARGET_PRODUCT_IDS = {"2BB0", "2BB2"}
TARGET_MODELS = {"MX420-D24-WTT", "MX480-D48-WTT"}


class HuaweiApiError(RuntimeError):
    """A sanitized error that cannot echo account or device data."""

    def __init__(self, stage: str, status: int | None = None) -> None:
        super().__init__(stage)
        self.stage = stage
        self.status = status


def build_authorization_url() -> tuple[str, str]:
    """Build the HMS Lite authorization URL used by the compatibility flow."""
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    query = urllib.parse.urlencode(
        {
            "access_type": "offline",
            "response_type": "code",
            "client_id": APP_ID,
            "ui_locales": "zh-cn",
            "redirect_uri": REDIRECT_URI,
            "state": state,
            "scope": " ".join(SCOPES),
            "display": "touch",
            "nonce": nonce,
            "include_granted_scopes": "true",
            "uuid": str(uuid.uuid4()),
            "countryCode": "CN",
            "cVersion": "HwID_6.10.0.300",
            "code_challenge": challenge.decode(),
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}", state


def authorization_code_from_input(value: str, expected_state: str | None = None) -> str:
    """Extract a one-time code from a code or the hms:// callback URL."""
    value = value.strip()
    if not value:
        raise ValueError("authorization input is empty")
    if "://" not in value:
        return value
    callback = urllib.parse.urlparse(value)
    if callback.scheme != "hms" or callback.netloc != "redirect_url":
        raise ValueError("callback must use hms://redirect_url")
    query = urllib.parse.parse_qs(callback.query)
    if expected_state is not None and query.get("state", [None])[0] != expected_state:
        raise ValueError("callback state does not match this authorization attempt")
    code = query.get("code", [""])[0].strip()
    if not code:
        raise ValueError("callback does not contain an authorization code")
    return code


def request_json(
    stage: str,
    method: str,
    url: str,
    *,
    access_token: str | None = None,
    body: Any | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    """Call a compatibility endpoint without logging response bodies."""
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": f"HuaweiSmartHome/{APP_VERSION}",
        "x-appId": APP_ID,
        "x-appVersion": APP_VERSION,
        "x-language": "zh-CN",
        "x-ori-app-name": "com.huawei.smarthome",
        "x-phoneOs": "Android",
        "x-requestId": str(uuid.uuid4()),
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if extra_headers:
        headers.update(extra_headers)
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        exc.read()
        raise HuaweiApiError(stage, exc.code) from None
    except (urllib.error.URLError, TimeoutError):
        raise HuaweiApiError(stage) from None
    try:
        return json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HuaweiApiError(f"{stage}-response") from None


def walk(value: Any) -> Iterable[Any]:
    """Walk nested JSON, including fields that themselves contain JSON."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                yield from walk(json.loads(text))
            except json.JSONDecodeError:
                pass
        return
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def collect_values(value: Any, keys: set[str]) -> list[str]:
    """Collect unique scalar values for any matching nested key."""
    found: list[str] = []
    for item in walk(value):
        if not isinstance(item, dict):
            continue
        for key in keys:
            candidate = item.get(key)
            if isinstance(candidate, (str, int)) and str(candidate).strip():
                found.append(str(candidate).strip())
    return list(dict.fromkeys(found))


def collect_device_records(value: Any) -> list[dict[str, str]]:
    """Normalize the subset of device metadata used by the helpers."""
    records: dict[str, dict[str, str]] = {}
    for item in walk(value):
        if not isinstance(item, dict):
            continue
        device_id = next(
            (
                str(item[key]).strip()
                for key in ("devId", "deviceId", "deviceID")
                if isinstance(item.get(key), (str, int)) and str(item[key]).strip()
            ),
            "",
        )
        if not device_id:
            continue
        record = records.setdefault(device_id, {"device_id": device_id})
        for source_key, target_key in (
            ("productId", "product_id"),
            ("prodId", "product_id"),
            ("model", "model"),
            ("deviceName", "name"),
            ("name", "name"),
        ):
            candidate = item.get(source_key)
            if target_key not in record and isinstance(candidate, (str, int)):
                record[target_key] = str(candidate)
    return list(records.values())


def collect_authcodes(value: Any, requested_ids: list[str]) -> list[dict[str, str]]:
    """Normalize auth-code records returned by different API response shapes."""
    result: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    for item in walk(value):
        if not isinstance(item, dict):
            continue
        auth_code = item.get("authCode")
        if not isinstance(auth_code, str) or not AUTH_RE.fullmatch(auth_code.strip()):
            continue
        auth_code = auth_code.strip()
        if auth_code in seen_codes:
            continue
        seen_codes.add(auth_code)
        record = {"auth_code": auth_code}
        for source_key in ("devId", "deviceId", "deviceID"):
            if isinstance(item.get(source_key), (str, int)):
                record["device_id"] = str(item[source_key]).strip()
                break
        if isinstance(item.get("authCodeId"), (str, int)):
            record["auth_code_id"] = str(item["authCodeId"])
        result.append(record)
    missing_all_device_ids = result and all("device_id" not in item for item in result)
    if missing_all_device_ids and len(result) == len(requested_ids):
        for record, device_id in zip(result, requested_ids, strict=True):
            record["device_id"] = device_id
    return result


def write_secret_file(path: Path, payload: dict[str, Any], *, overwrite: bool = False) -> None:
    """Atomically create a mode-0600 credential file."""
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="credential JSON path; choose an encrypted/private directory outside Git",
    )
    parser.add_argument(
        "--start-authorization",
        action="store_true",
        help="print a fresh official Huawei authorization URL before prompting",
    )
    parser.add_argument(
        "--acknowledge-unsupported-api",
        action="store_true",
        help="confirm this compatibility workflow is unofficial and may change",
    )
    parser.add_argument("--force", action="store_true", help="replace an existing output file")
    return parser.parse_args()


def fetch_credentials(
    one_time_code: str,
    *,
    include_names: bool = False,
) -> tuple[list[dict[str, str]], int, int]:
    """Exchange an authorization code and fetch eligible local credentials."""
    token_response = request_json(
        "token",
        "POST",
        TOKEN_URL,
        body={"appId": APP_ID, "code": urllib.parse.quote(one_time_code, safe="")},
    )
    access_token = token_response.get("access_token") if isinstance(token_response, dict) else None
    if not isinstance(access_token, str) or not access_token:
        raise HuaweiApiError("token-shape")

    homes_response = request_json("homes", "GET", HOMES_URL, access_token=access_token)
    home_ids = collect_values(homes_response, {"homeId"})

    all_devices: list[dict[str, str]] = []
    for home_id in home_ids:
        response = request_json(
            "devices-v2",
            "GET",
            DEVICES_V2_URL,
            access_token=access_token,
            extra_headers={"x-homeId": home_id},
        )
        all_devices.extend(collect_device_records(response))

    if home_ids:
        query = urllib.parse.urlencode({"homeId": ",".join(home_ids)})
        response = request_json(
            "devices-v3",
            "GET",
            f"{DEVICES_V3_URL}?{query}",
            access_token=access_token,
        )
        all_devices.extend(collect_device_records(response))

    devices_by_id: dict[str, dict[str, str]] = {}
    for record in all_devices:
        current = devices_by_id.setdefault(record["device_id"], {"device_id": record["device_id"]})
        current.update(record)
    devices = list(devices_by_id.values())
    if not devices:
        raise HuaweiApiError("devices-empty")

    targets = [
        record
        for record in devices
        if record.get("product_id", "").upper() in TARGET_PRODUCT_IDS
        or record.get("model", "").upper() in TARGET_MODELS
    ]
    if not targets:
        targets = devices
    requested_ids = [record["device_id"] for record in targets]
    auth_response = request_json(
        "authcodes",
        "POST",
        AUTHCODE_URL,
        access_token=access_token,
        body={"devIds": requested_ids},
    )
    credentials = collect_authcodes(auth_response, requested_ids)
    if not credentials:
        raise HuaweiApiError("authcodes-empty")

    metadata = {record["device_id"]: record for record in targets}
    for record in credentials:
        device_id = record.get("device_id")
        if device_id in metadata:
            metadata_keys = (
                ("product_id", "model", "name")
                if include_names
                else ("product_id", "model")
            )
            for key in metadata_keys:
                if key in metadata[device_id]:
                    record[key] = metadata[device_id][key]
    return credentials, len(home_ids), len(devices)


def main() -> int:
    """Run the one-time credential acquisition workflow."""
    args = parse_args()
    if not args.acknowledge_unsupported_api:
        print("ERROR stage=acknowledgement-required")
        return 2
    if args.output.exists() and not args.force:
        print("ERROR stage=output-exists")
        return 2

    expected_state: str | None = None
    if args.start_authorization:
        url, expected_state = build_authorization_url()
        print("Open this URL in a private browser window and authorize only on Huawei's domain:")
        print(url)
        prompt = "Paste the final hms:// callback URL (hidden): "
    else:
        prompt = "Huawei one-time authorization code or hms:// callback URL (hidden): "
    authorization_input = getpass.getpass(prompt)
    try:
        one_time_code = authorization_code_from_input(authorization_input, expected_state)
    except ValueError:
        print("ERROR stage=authorization-input")
        return 2

    try:
        credentials, home_count, device_count = fetch_credentials(one_time_code)
        write_secret_file(
            args.output,
            {
                "created_at": datetime.now(UTC).isoformat(),
                "source": "Huawei Smart Life compatibility authorization",
                "devices": credentials,
            },
            overwrite=args.force,
        )
    except FileExistsError:
        print("ERROR stage=output-exists")
        return 2
    except HuaweiApiError as exc:
        suffix = f" http={exc.status}" if exc.status else ""
        print(f"ERROR stage={exc.stage}{suffix}")
        return 1

    print(f"OK stage=homes count={home_count}")
    print(f"OK stage=devices count={device_count}")
    print(f"OK stage=credentials count={len(credentials)}")
    print("OK stage=saved mode=0600")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
