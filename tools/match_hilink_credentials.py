#!/usr/bin/env python3
"""Match one lamp IP to a credential record using read-only HiLink requests."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

CLIENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "huawei_hilink_opple"
sys.path.insert(0, str(CLIENT_DIR))

from client import (  # noqa: E402
    CoapMessage,
    HiLinkAuthenticationError,
    HiLinkError,
    HiLinkLegacyClient,
    LightState,
    _parse_coap,
)


class AdbRelayClient(HiLinkLegacyClient):
    """Use an authorized Android phone as the UDP path to an isolated IoT client."""

    def __init__(self, *args: Any, adb_path: str, adb_serial: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.adb_path = adb_path
        self.adb_serial = adb_serial

    def _adb(self, *arguments: str, timeout: float, capture: bool = False) -> bytes:
        result = subprocess.run(
            [self.adb_path, "-s", self.adb_serial, *arguments],
            check=False,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise HiLinkError("Android relay command failed")
        return result.stdout if capture else b""

    def _exchange(self, packet: bytes, token: bytes) -> CoapMessage:
        suffix = f"{os.getpid()}_{secrets.token_hex(4)}"
        remote_request = f"/data/local/tmp/hilink_request_{suffix}.bin"
        remote_response = f"/data/local/tmp/hilink_response_{suffix}.bin"
        command = (
            f"toybox nc -u -q 2 -W 3 {self.host} {self.port} "
            f"<{remote_request} >{remote_response}"
        )
        try:
            with tempfile.NamedTemporaryFile() as request_file:
                request_file.write(packet)
                request_file.flush()
                self._adb(
                    "push",
                    request_file.name,
                    remote_request,
                    timeout=self.timeout + 8,
                )
            self._adb("shell", command, timeout=self.timeout + 8)
            response = self._adb(
                "exec-out",
                "toybox",
                "cat",
                remote_response,
                timeout=self.timeout + 8,
                capture=True,
            )
        finally:
            try:
                self._adb(
                    "shell",
                    f"toybox rm -f {remote_request} {remote_response}",
                    timeout=self.timeout + 8,
                )
            except (HiLinkError, subprocess.TimeoutExpired):
                pass
        if not response:
            raise HiLinkError("Android relay returned no data")
        message = _parse_coap(response)
        if message.token != token:
            raise HiLinkError("Android relay returned the wrong token")
        return message


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="lamp IPv4 address")
    parser.add_argument("--credentials-file", required=True, type=Path)
    parser.add_argument("--index", type=int, help="try only one zero-based credential record")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--adb-serial", help="route UDP through this wirelessly-debugged phone")
    parser.add_argument("--adb-path", default="adb")
    return parser.parse_args()


def load_credentials(path: Path) -> list[dict[str, str]]:
    """Load and minimally validate a protected credential file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    devices = payload.get("devices") if isinstance(payload, dict) else None
    if not isinstance(devices, list) or not devices:
        raise ValueError("credential file has no devices")
    records: list[dict[str, str]] = []
    for item in devices:
        if not isinstance(item, dict):
            continue
        device_id = item.get("device_id")
        auth_code = item.get("auth_code")
        if isinstance(device_id, str) and isinstance(auth_code, str):
            records.append({"device_id": device_id, "auth_code": auth_code})
    if not records:
        raise ValueError("credential file has no usable records")
    return records


def build_client(
    host: str,
    record: dict[str, str],
    timeout: float,
    adb_serial: str | None,
    adb_path: str,
) -> HiLinkLegacyClient:
    """Create a direct or Android-relayed read-only client."""
    common: dict[str, Any] = {
        "host": host,
        "device_id": record["device_id"],
        "auth_code": record["auth_code"],
        "timeout": timeout,
    }
    if adb_serial:
        return AdbRelayClient(**common, adb_path=adb_path, adb_serial=adb_serial)
    return HiLinkLegacyClient(**common)


def print_match(index: int, state: LightState) -> None:
    """Print only normalized non-credential state."""
    print(
        "MATCH "
        f"index={index} "
        f"on={str(state.is_on).lower()} "
        f"brightness_percent={state.brightness} "
        f"color_temp_kelvin={state.color_temp_kelvin}"
    )


def main() -> int:
    """Try credential records until authenticated state reading succeeds."""
    args = parse_args()
    try:
        host = str(ipaddress.IPv4Address(args.host))
        records = load_credentials(args.credentials_file)
    except (OSError, ValueError, json.JSONDecodeError):
        print("ERROR stage=input")
        return 2
    if args.index is not None:
        if args.index < 0 or args.index >= len(records):
            print("ERROR stage=index")
            return 2
        candidates = [(args.index, records[args.index])]
    else:
        candidates = list(enumerate(records))

    for index, record in candidates:
        try:
            client = build_client(host, record, args.timeout, args.adb_serial, args.adb_path)
            client.create_session()
            state = client.read_state()
        except HiLinkAuthenticationError:
            print(f"MISS index={index} category=authentication")
            continue
        except (TimeoutError, HiLinkError, OSError, subprocess.TimeoutExpired):
            print(f"MISS index={index} category=network-or-protocol")
            continue
        print_match(index, state)
        return 0
    print("ERROR no-match")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
