"""Huawei HiLink legacy CoAP local-control client."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import socket
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

COAP_PORT = 5686
DEFAULT_REQUEST_TIMEOUT = 3.5
CONTENT_FORMAT_JSON = 50
OPT_URI_PATH = 11
OPT_CONTENT_FORMAT = 12
OPT_SESSION_ID = 2048
OPT_REQUEST_ID = 2050
OPT_DEVICE_ID = 2051
OPT_SEQUENCE = 2053
OPT_PHONE_ACCOUNT_ID = 2056


class HiLinkError(RuntimeError):
    """Base error for the local HiLink protocol."""


class HiLinkAuthenticationError(HiLinkError):
    """The device rejected the supplied local auth code."""


@dataclass(frozen=True, slots=True)
class LightState:
    """Normalized state reported by an Opple light."""

    is_on: bool
    brightness: int
    color_temp_kelvin: int


@dataclass(frozen=True, slots=True)
class CoapMessage:
    """Decoded CoAP datagram."""

    raw: bytes
    code: int
    token: bytes
    options: tuple[tuple[int, bytes], ...]
    payload: bytes

    def option(self, number: int) -> bytes | None:
        """Return the first matching option."""
        for candidate, value in self.options:
            if candidate == number:
                return value
        return None


def _option_nibble(value: int) -> tuple[int, bytes]:
    if value < 13:
        return value, b""
    if value < 269:
        return 13, bytes((value - 13,))
    if value < 65805:
        return 14, (value - 269).to_bytes(2, "big")
    raise ValueError("CoAP option is too large")


def _uint_bytes(value: int) -> bytes:
    if value < 0:
        raise ValueError("CoAP uint option cannot be negative")
    if value == 0:
        return b""
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


def _encode_options(options: list[tuple[int, bytes]]) -> bytes:
    encoded = bytearray()
    previous = 0
    for number, value in sorted(options, key=lambda item: item[0]):
        delta_nibble, delta_extra = _option_nibble(number - previous)
        length_nibble, length_extra = _option_nibble(len(value))
        encoded.append((delta_nibble << 4) | length_nibble)
        encoded += delta_extra + length_extra + value
        previous = number
    return bytes(encoded)


def _build_coap(
    *,
    code: int,
    message_id: int,
    token: bytes,
    options: list[tuple[int, bytes]],
    payload: bytes = b"",
) -> bytes:
    if not 0 <= len(token) <= 8:
        raise ValueError("CoAP token length must be 0..8")
    packet = bytearray(((1 << 6) | len(token), code))
    packet += message_id.to_bytes(2, "big")
    packet += token
    packet += _encode_options(options)
    if payload:
        packet.append(0xFF)
        packet += payload
    return bytes(packet)


def _read_extended(nibble: int, data: bytes, offset: int) -> tuple[int, int]:
    if nibble < 13:
        return nibble, offset
    if nibble == 13:
        return data[offset] + 13, offset + 1
    if nibble == 14:
        return int.from_bytes(data[offset : offset + 2], "big") + 269, offset + 2
    raise ValueError("Reserved CoAP option nibble")


def _parse_coap(data: bytes) -> CoapMessage:
    if len(data) < 4 or data[0] >> 6 != 1:
        raise HiLinkError("Invalid CoAP response")
    token_length = data[0] & 0x0F
    offset = 4 + token_length
    if token_length > 8 or offset > len(data):
        raise HiLinkError("Invalid CoAP token")
    token = data[4:offset]
    number = 0
    options: list[tuple[int, bytes]] = []
    has_payload = False
    while offset < len(data):
        if data[offset] == 0xFF:
            offset += 1
            has_payload = True
            break
        header = data[offset]
        offset += 1
        delta, offset = _read_extended(header >> 4, data, offset)
        length, offset = _read_extended(header & 0x0F, data, offset)
        number += delta
        if offset + length > len(data):
            raise HiLinkError("Truncated CoAP option")
        options.append((number, data[offset : offset + length]))
        offset += length
    return CoapMessage(data, data[1], token, tuple(options), data[offset:] if has_payload else b"")


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _pbkdf2(password: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password, salt, 1, 32)


def _aes_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _aes_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def _walk_json(value: Any) -> Iterable[Any]:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                yield from _walk_json(json.loads(text))
            except json.JSONDecodeError:
                pass
        return
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def parse_light_state(response: dict[str, Any]) -> LightState:
    """Normalize the nested/stringified service list from devDataInfo."""
    services: dict[str, dict[str, Any]] = {}
    for item in _walk_json(response):
        if not isinstance(item, dict):
            continue
        service_id = item.get("sid", item.get("serviceId"))
        if not isinstance(service_id, str):
            continue
        data = item.get("data", item)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                continue
        if isinstance(data, dict):
            services[service_id] = data

    def value(service: str, key: str) -> Any:
        if service in services and key in services[service]:
            return services[service][key]
        for item in _walk_json(response):
            if isinstance(item, dict) and key in item:
                return item[key]
        return None

    raw_on = value("switch", "on")
    if isinstance(raw_on, str):
        is_on = raw_on.strip().lower() in {"1", "true", "on"}
    else:
        is_on = bool(raw_on)
    try:
        brightness = max(0, min(100, int(value("brightness", "brightness"))))
        color_temp = max(2700, min(5700, int(value("cct", "colorTemperature"))))
    except (TypeError, ValueError) as exc:
        raise HiLinkError("Device state is missing brightness or color temperature") from exc
    return LightState(is_on, brightness, color_temp)


class HiLinkLegacyClient:
    """Synchronous client for one legacy Wi-Fi HiLink device."""

    def __init__(
        self,
        host: str,
        device_id: str,
        auth_code: str,
        *,
        port: int = COAP_PORT,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.device_id = device_id
        try:
            self.auth_code = bytes.fromhex(auth_code)
        except ValueError as exc:
            raise ValueError("auth_code must be hexadecimal") from exc
        if not self.auth_code:
            raise ValueError("auth_code cannot be empty")
        self.phone_account_id = secrets.token_hex(8)
        self.timeout = timeout
        self.session_id = ""
        self.app_sequence = 0
        self.device_sequence = 0
        self.aes_key = b""
        self.hmac_key = b""

    def _exchange(self, packet: bytes, token: bytes) -> CoapMessage:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(self.timeout)
                sock.sendto(packet, (self.host, self.port))
                while True:
                    data, peer = sock.recvfrom(8192)
                    if peer[0] != self.host:
                        continue
                    message = _parse_coap(data)
                    if message.token == token:
                        return message
        except (OSError, TimeoutError) as exc:
            raise HiLinkError("No local response from device") from exc

    def create_session(self) -> None:
        """Negotiate AES and HMAC keys using the device's local auth code."""
        sn1 = secrets.token_bytes(8)
        requested_sequence = secrets.randbelow(32767)
        token = secrets.token_bytes(3)
        packet = _build_coap(
            code=2,
            message_id=secrets.randbelow(65536),
            token=token,
            options=[
                (OPT_URI_PATH, b".sys"),
                (OPT_URI_PATH, b"sessMngr"),
                (OPT_CONTENT_FORMAT, _uint_bytes(CONTENT_FORMAT_JSON)),
                (OPT_PHONE_ACCOUNT_ID, self.phone_account_id.encode()),
            ],
            payload=_json_bytes(
                {
                    "type": 1,
                    "modeSupport": 3,
                    "sn1": sn1.hex(),
                    "seq": requested_sequence,
                    "confirm": 1,
                }
            ),
        )
        response = self._exchange(packet, token)
        try:
            body = json.loads(response.payload.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HiLinkError("Invalid session response") from exc
        if body.get("errcode") != 0 or body.get("modeResp") != 1:
            raise HiLinkAuthenticationError("Device rejected local session authentication")
        try:
            self.session_id = str(body["sessId"])
            sn2 = bytes.fromhex(str(body["sn2"]))
            salt = sn1 + sn2
            digest = _pbkdf2(self.auth_code, salt)
            self.aes_key = digest[:16]
            self.hmac_key = _pbkdf2(self.aes_key, salt)
            self.app_sequence = int(body["seq"])
            self.device_sequence = requested_sequence
        except (KeyError, TypeError, ValueError) as exc:
            raise HiLinkError("Incomplete session response") from exc
        if int(body.get("confirm", 0)) == 1:
            confirm = self._secure_request(".sysConfirm", {"devId": self.device_id})
            if confirm.get("errcode") != 0:
                raise HiLinkAuthenticationError("Device rejected session confirmation")

    def _secure_request(self, service_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if not self.session_id or not self.aes_key or not self.hmac_key:
            raise HiLinkError("A local session has not been created")
        token = secrets.token_bytes(3)
        message_id = secrets.randbelow(65536)
        iv = secrets.token_bytes(16)
        encrypted = _aes_encrypt(_json_bytes(data), self.aes_key, iv) + iv
        options = [
            *((OPT_URI_PATH, part.encode()) for part in service_id.split("/") if part),
            (OPT_CONTENT_FORMAT, _uint_bytes(CONTENT_FORMAT_JSON)),
            (OPT_SESSION_ID, self.session_id.encode()),
            (OPT_REQUEST_ID, str(uuid.uuid4()).encode()),
            (OPT_DEVICE_ID, self.device_id.encode()),
            (OPT_SEQUENCE, _uint_bytes(self.app_sequence + 1)),
            (OPT_PHONE_ACCOUNT_ID, self.phone_account_id.encode()),
        ]
        authenticated = _build_coap(
            code=2,
            message_id=message_id,
            token=token,
            options=options,
            payload=encrypted,
        )
        mac = hmac.new(self.hmac_key, authenticated, hashlib.sha256).digest()
        response = self._exchange(
            _build_coap(
                code=2,
                message_id=message_id,
                token=token,
                options=options,
                payload=encrypted + mac,
            ),
            token,
        )
        if len(response.payload) < 48:
            raise HiLinkAuthenticationError("Secure response was rejected")
        expected = hmac.new(self.hmac_key, response.raw[:-32], hashlib.sha256).digest()
        if not hmac.compare_digest(response.payload[-32:], expected):
            raise HiLinkAuthenticationError("Secure response signature is invalid")
        secured = response.payload[:-32]
        try:
            plaintext = _aes_decrypt(secured[:-16], self.aes_key, secured[-16:])
            body = json.loads(plaintext.decode().strip())
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HiLinkAuthenticationError("Secure response could not be decrypted") from exc
        response_sequence = response.option(OPT_SEQUENCE)
        if response_sequence is not None:
            candidate = int.from_bytes(response_sequence, "big")
            if abs(candidate - self.device_sequence) < 30:
                self.device_sequence = candidate
        self.app_sequence += 1
        self.device_sequence += 1
        if not isinstance(body, dict):
            raise HiLinkError("Unexpected secure response")
        return body

    def read_state(self) -> LightState:
        """Read all service values and return the normalized light state."""
        body = self._secure_request("devDataInfo", {"type": "allSevice"})
        if body.get("errcode") not in (None, 0):
            raise HiLinkError("Device rejected state request")
        return parse_light_state(body)

    def set_service(self, service_id: str, data: dict[str, Any]) -> None:
        """Set one service value."""
        body = self._secure_request(service_id, data)
        if body.get("errcode") not in (None, 0):
            raise HiLinkError(f"Device rejected {service_id} command")
