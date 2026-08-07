#!/usr/bin/env python3

"""Compare provider fetch behavior with the locked official Mihomo binary.

The acceptance test compares a raw plaintext HTTP/1.1 request and the complete
TLS ClientHello sent for an HTTPS provider. The TLS endpoint deliberately stops
the handshake before certificates; HTTP/2 is outside this test's scope.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from package_mihomo_fetcher import (  # noqa: E402
    PackagingError,
    _download_asset,
    _oracle_executable,
    _validate_binary_platform,
    install_locked,
    load_and_validate_lock,
)


DEFAULT_LOCK = ROOT / ".github" / "source-lock.json"
DEFAULT_CACHE = ROOT / ".cache" / "mihomo-assets"
MAX_FRAME = 4 * 1024 * 1024
MAX_HTTP_HEAD = 64 * 1024
MAX_TLS_RECORD = (1 << 14) + 256
MAX_CLIENT_HELLO = 1024 * 1024
QUIET_PERIOD_SECONDS = 0.25
PROVIDER_BODY = (
    b"proxies:\n"
    b"  - name: oracle-node\n"
    b"    type: ss\n"
    b"    server: 127.0.0.1\n"
    b"    port: 8388\n"
    b"    cipher: aes-128-gcm\n"
    b"    password: password\n"
)
EXPECTED_CAPABILITIES = [
    "direct",
    "http-proxy",
    "https-proxy",
    "socks5-proxy",
    "etag",
    "raw-body",
    "response-headers",
]
TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
STABLE_TAG = re.compile(r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
TLS_EXTENSION_NAMES = {
    0: "server_name",
    5: "status_request",
    10: "supported_groups",
    11: "ec_point_formats",
    13: "signature_algorithms",
    16: "alpn",
    18: "signed_certificate_timestamp",
    23: "extended_master_secret",
    27: "compress_certificate",
    35: "session_ticket",
    43: "supported_versions",
    45: "psk_key_exchange_modes",
    50: "signature_algorithms_cert",
    51: "key_share",
    65281: "renegotiation_info",
}


class OracleParityError(RuntimeError):
    """Raised when runtime evidence is incomplete or differs from the oracle."""


@dataclass(frozen=True)
class HTTPRequestCapture:
    method: str
    raw_target: str
    version: str
    headers: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "raw_target": self.raw_target,
            "version": self.version,
            "headers": [
                {"name": name, "value": value} for name, value in self.headers
            ],
        }


@dataclass(frozen=True)
class TLSClientHelloCapture:
    destination_host: str
    destination_port: int
    record_versions: tuple[int, ...]
    record_payload_lengths: tuple[int, ...]
    legacy_version: int
    random: bytes
    session_id: bytes
    cipher_suites: tuple[int, ...]
    compression_methods: tuple[int, ...]
    extensions: tuple[tuple[int, bytes], ...]


class _TLSReader:
    def __init__(self, payload: bytes, label: str):
        self.payload = payload
        self.label = label
        self.offset = 0

    @property
    def remaining(self) -> int:
        return len(self.payload) - self.offset

    def take(self, length: int) -> bytes:
        end = self.offset + length
        if length < 0 or end > len(self.payload):
            raise OracleParityError("{} is truncated".format(self.label))
        result = self.payload[self.offset : end]
        self.offset = end
        return result

    def uint8(self) -> int:
        return self.take(1)[0]

    def uint16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def vector8(self) -> bytes:
        return self.take(self.uint8())

    def vector16(self) -> bytes:
        return self.take(self.uint16())

    def require_end(self) -> None:
        if self.remaining != 0:
            raise OracleParityError("{} contains trailing bytes".format(self.label))


def _parse_uint16_vector(payload: bytes, label: str) -> tuple[int, ...]:
    reader = _TLSReader(payload, label)
    values = reader.vector16()
    reader.require_end()
    if not values or len(values) % 2:
        raise OracleParityError("{} has an invalid uint16 vector".format(label))
    return tuple(
        struct.unpack(">H", values[index : index + 2])[0]
        for index in range(0, len(values), 2)
    )


def _parse_server_names(payload: bytes) -> list[dict[str, Any]]:
    reader = _TLSReader(payload, "server_name extension")
    names_reader = _TLSReader(reader.vector16(), "server_name list")
    reader.require_end()
    names = []
    while names_reader.remaining:
        name_type = names_reader.uint8()
        name = names_reader.vector16()
        names.append({"type": name_type, "value_hex": name.hex()})
    if not names:
        raise OracleParityError("server_name extension has an empty name list")
    return names


def _parse_alpn(payload: bytes) -> list[str]:
    reader = _TLSReader(payload, "ALPN extension")
    protocols_reader = _TLSReader(reader.vector16(), "ALPN protocol list")
    reader.require_end()
    protocols = []
    while protocols_reader.remaining:
        protocol = protocols_reader.vector8()
        if not protocol:
            raise OracleParityError("ALPN contains an empty protocol")
        protocols.append(protocol.hex())
    if not protocols:
        raise OracleParityError("ALPN protocol list is empty")
    return protocols


def _parse_supported_versions(payload: bytes) -> list[int]:
    reader = _TLSReader(payload, "supported_versions extension")
    versions = reader.vector8()
    reader.require_end()
    if not versions or len(versions) % 2:
        raise OracleParityError("supported_versions vector is invalid")
    return [
        struct.unpack(">H", versions[index : index + 2])[0]
        for index in range(0, len(versions), 2)
    ]


def _normalize_key_share(payload: bytes) -> tuple[bytes, list[dict[str, Any]]]:
    reader = _TLSReader(payload, "key_share extension")
    shares_payload = reader.vector16()
    reader.require_end()
    shares_reader = _TLSReader(shares_payload, "key_share client_shares")
    normalized_shares = bytearray()
    shares = []
    while shares_reader.remaining:
        group = shares_reader.uint16()
        key_exchange = shares_reader.vector16()
        if not key_exchange:
            raise OracleParityError("key_share contains an empty key exchange")
        normalized_shares.extend(struct.pack(">H", group))
        normalized_shares.extend(struct.pack(">H", len(key_exchange)))
        normalized_shares.extend(b"\x00" * len(key_exchange))
        shares.append(
            {
                "group": group,
                "key_exchange": "<ephemeral-bytes:{}>".format(len(key_exchange)),
            }
        )
    if not shares:
        raise OracleParityError("key_share client_shares is empty")
    return struct.pack(">H", len(normalized_shares)) + normalized_shares, shares


def _extension_json(extension_type: int, payload: bytes) -> dict[str, Any]:
    normalized_payload = payload
    decoded: Any = None
    if extension_type == 0:
        decoded = _parse_server_names(payload)
    elif extension_type == 10:
        decoded = list(_parse_uint16_vector(payload, "supported_groups extension"))
    elif extension_type in {13, 50}:
        decoded = list(
            _parse_uint16_vector(payload, "signature_algorithms extension")
        )
    elif extension_type == 16:
        decoded = _parse_alpn(payload)
    elif extension_type == 43:
        decoded = _parse_supported_versions(payload)
    elif extension_type == 51:
        normalized_payload, decoded = _normalize_key_share(payload)
    result = {
        "type": extension_type,
        "name": TLS_EXTENSION_NAMES.get(extension_type, "unknown"),
        "length": len(payload),
        "data_hex": normalized_payload.hex(),
    }
    if decoded is not None:
        result["decoded"] = decoded
    return result


def parse_tls_client_hello_records(
    records: list[tuple[int, bytes]], *, destination_host: str, destination_port: int
) -> TLSClientHelloCapture:
    if not records:
        raise OracleParityError("TLS capture contains no handshake records")
    handshake = b"".join(payload for _version, payload in records)
    if len(handshake) < 4 or handshake[0] != 1:
        raise OracleParityError("TLS handshake does not start with ClientHello")
    handshake_length = int.from_bytes(handshake[1:4], "big")
    if handshake_length <= 0 or handshake_length > MAX_CLIENT_HELLO:
        raise OracleParityError("TLS ClientHello length is invalid")
    if len(handshake) != 4 + handshake_length:
        raise OracleParityError("TLS ClientHello capture is incomplete or has trailing data")

    reader = _TLSReader(handshake[4:], "TLS ClientHello")
    legacy_version = reader.uint16()
    random = reader.take(32)
    session_id = reader.vector8()
    if len(session_id) > 32:
        raise OracleParityError("TLS ClientHello session id exceeds 32 bytes")
    cipher_bytes = reader.vector16()
    if not cipher_bytes or len(cipher_bytes) % 2:
        raise OracleParityError("TLS ClientHello cipher suite vector is invalid")
    cipher_suites = tuple(
        struct.unpack(">H", cipher_bytes[index : index + 2])[0]
        for index in range(0, len(cipher_bytes), 2)
    )
    compression = reader.vector8()
    if not compression:
        raise OracleParityError("TLS ClientHello compression vector is empty")

    extensions: list[tuple[int, bytes]] = []
    if reader.remaining:
        extensions_reader = _TLSReader(
            reader.vector16(), "TLS ClientHello extensions"
        )
        reader.require_end()
        seen = set()
        while extensions_reader.remaining:
            extension_type = extensions_reader.uint16()
            if extension_type in seen:
                raise OracleParityError("TLS ClientHello contains duplicate extensions")
            seen.add(extension_type)
            payload = extensions_reader.vector16()
            _extension_json(extension_type, payload)
            extensions.append((extension_type, payload))
    reader.require_end()

    return TLSClientHelloCapture(
        destination_host=destination_host,
        destination_port=destination_port,
        record_versions=tuple(version for version, _payload in records),
        record_payload_lengths=tuple(len(payload) for _version, payload in records),
        legacy_version=legacy_version,
        random=random,
        session_id=session_id,
        cipher_suites=cipher_suites,
        compression_methods=tuple(compression),
        extensions=tuple(extensions),
    )


def normalize_tls_client_hello(capture: TLSClientHelloCapture) -> dict[str, Any]:
    if capture.destination_host != "127.0.0.1" or capture.destination_port <= 0:
        raise OracleParityError("TLS capture destination is not controlled loopback")
    if len(capture.random) != 32:
        raise OracleParityError("TLS ClientHello random is not 32 bytes")
    return {
        "destination_host": capture.destination_host,
        "destination_port": "<dynamic-port>",
        "record_versions": [
            "0x{:04x}".format(version) for version in capture.record_versions
        ],
        "record_payload_lengths": list(capture.record_payload_lengths),
        "legacy_version": "0x{:04x}".format(capture.legacy_version),
        "random": "<client-random:32-bytes>",
        "session_id": "<session-id:{}-bytes>".format(len(capture.session_id)),
        "cipher_suites": list(capture.cipher_suites),
        "compression_methods": list(capture.compression_methods),
        "extensions": [
            _extension_json(extension_type, payload)
            for extension_type, payload in capture.extensions
        ],
    }


def compare_tls_client_hellos(
    official: TLSClientHelloCapture, helper: TLSClientHelloCapture
) -> dict[str, Any]:
    normalized_official = normalize_tls_client_hello(official)
    normalized_helper = normalize_tls_client_hello(helper)
    if normalized_official != normalized_helper:
        raise OracleParityError(
            "helper TLS ClientHello differs from official Mihomo:\n"
            + json.dumps(
                {
                    "official": normalized_official,
                    "helper": normalized_helper,
                },
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
        )
    return normalized_official


def _cbor_head(major: int, value: int) -> bytes:
    if value < 0:
        raise OracleParityError("CBOR length cannot be negative")
    if value < 24:
        return bytes([(major << 5) | value])
    if value <= 0xFF:
        return bytes([(major << 5) | 24, value])
    if value <= 0xFFFF:
        return bytes([(major << 5) | 25]) + struct.pack(">H", value)
    if value <= 0xFFFFFFFF:
        return bytes([(major << 5) | 26]) + struct.pack(">I", value)
    if value <= 0xFFFFFFFFFFFFFFFF:
        return bytes([(major << 5) | 27]) + struct.pack(">Q", value)
    raise OracleParityError("CBOR integer exceeds uint64")


def encode_cbor(value: Any) -> bytes:
    if value is None:
        return b"\xf6"
    if value is False:
        return b"\xf4"
    if value is True:
        return b"\xf5"
    if isinstance(value, int):
        if value >= 0:
            return _cbor_head(0, value)
        return _cbor_head(1, -1 - value)
    if isinstance(value, bytes):
        return _cbor_head(2, len(value)) + value
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return _cbor_head(3, len(encoded)) + encoded
    if isinstance(value, (list, tuple)):
        return _cbor_head(4, len(value)) + b"".join(
            encode_cbor(item) for item in value
        )
    if isinstance(value, dict):
        return _cbor_head(5, len(value)) + b"".join(
            encode_cbor(key) + encode_cbor(item) for key, item in value.items()
        )
    raise OracleParityError("unsupported CBOR value type: {}".format(type(value)))


class _CBORDecoder:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    def _take(self, length: int) -> bytes:
        end = self.offset + length
        if length < 0 or end > len(self.payload):
            raise OracleParityError("truncated CBOR payload")
        result = self.payload[self.offset : end]
        self.offset = end
        return result

    def _length(self, additional: int) -> int:
        if additional < 24:
            return additional
        if additional == 24:
            return self._take(1)[0]
        if additional == 25:
            return struct.unpack(">H", self._take(2))[0]
        if additional == 26:
            return struct.unpack(">I", self._take(4))[0]
        if additional == 27:
            return struct.unpack(">Q", self._take(8))[0]
        raise OracleParityError("indefinite or reserved CBOR lengths are forbidden")

    def decode(self) -> Any:
        initial = self._take(1)[0]
        major = initial >> 5
        additional = initial & 0x1F
        if major == 7:
            simple = {20: False, 21: True, 22: None}
            if additional not in simple:
                raise OracleParityError("unsupported CBOR simple value")
            return simple[additional]

        length = self._length(additional)
        if major == 0:
            return length
        if major == 1:
            return -1 - length
        if major == 2:
            return self._take(length)
        if major == 3:
            try:
                return self._take(length).decode("utf-8")
            except UnicodeDecodeError as error:
                raise OracleParityError("CBOR text is not valid UTF-8") from error
        if major == 4:
            return [self.decode() for _ in range(length)]
        if major == 5:
            result = {}
            for _ in range(length):
                key = self.decode()
                try:
                    duplicate = key in result
                except TypeError as error:
                    raise OracleParityError("CBOR map key is not scalar") from error
                if duplicate:
                    raise OracleParityError("CBOR map contains a duplicate key")
                result[key] = self.decode()
            return result
        raise OracleParityError("unsupported CBOR major type")


def decode_cbor(payload: bytes) -> Any:
    decoder = _CBORDecoder(payload)
    value = decoder.decode()
    if decoder.offset != len(payload):
        raise OracleParityError("CBOR payload contains trailing bytes")
    return value


def _read_exact(stream: BinaryIO, length: int, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    result = bytearray()
    descriptor = stream.fileno()
    while len(result) < length:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OracleParityError("timed out reading helper frame")
        ready, _, _ = select.select([descriptor], [], [], remaining)
        if not ready:
            raise OracleParityError("timed out reading helper frame")
        chunk = os.read(descriptor, length - len(result))
        if not chunk:
            raise OracleParityError("helper stdout closed during a frame")
        result.extend(chunk)
    return bytes(result)


def read_frame(stream: BinaryIO, timeout: float) -> Any:
    length = struct.unpack(">I", _read_exact(stream, 4, timeout))[0]
    if length == 0 or length > MAX_FRAME:
        raise OracleParityError("helper emitted an invalid frame length")
    return decode_cbor(_read_exact(stream, length, timeout))


def write_frame(stream: BinaryIO, value: Any) -> None:
    payload = encode_cbor(value)
    if not payload or len(payload) > MAX_FRAME:
        raise OracleParityError("helper request frame length is invalid")
    stream.write(struct.pack(">I", len(payload)) + payload)
    stream.flush()


def parse_http_request(raw_head: bytes) -> HTTPRequestCapture:
    if not raw_head.endswith(b"\r\n\r\n"):
        raise OracleParityError("HTTP capture lacks a complete CRLF header block")
    lines = raw_head[:-4].split(b"\r\n")
    if not lines or not lines[0]:
        raise OracleParityError("HTTP capture lacks a request line")
    try:
        request_line = lines[0].decode("ascii")
    except UnicodeDecodeError as error:
        raise OracleParityError("HTTP request line is not ASCII") from error
    parts = request_line.split(" ")
    if len(parts) != 3 or not all(parts):
        raise OracleParityError("HTTP request line is malformed")
    method, raw_target, version = parts

    headers: list[tuple[str, str]] = []
    for raw_line in lines[1:]:
        if not raw_line or raw_line[:1] in {b" ", b"\t"} or b":" not in raw_line:
            raise OracleParityError("HTTP capture contains a malformed header")
        raw_name, raw_value = raw_line.split(b":", 1)
        try:
            name = raw_name.decode("ascii")
            value = raw_value.decode("iso-8859-1").strip(" \t")
        except UnicodeDecodeError as error:
            raise OracleParityError("HTTP header name is not ASCII") from error
        if TOKEN.fullmatch(name) is None:
            raise OracleParityError("HTTP capture contains an invalid header name")
        if "\r" in value or "\n" in value:
            raise OracleParityError("HTTP capture contains a folded header value")
        headers.append((name, value))
    return HTTPRequestCapture(method, raw_target, version, tuple(headers))


def normalize_request(
    request: HTTPRequestCapture, *, authority: str
) -> HTTPRequestCapture:
    host_indexes = [
        index
        for index, (name, _value) in enumerate(request.headers)
        if name.lower() == "host"
    ]
    if len(host_indexes) != 1:
        raise OracleParityError("request must contain exactly one Host header")
    host_index = host_indexes[0]
    if request.headers[host_index][1] != authority:
        raise OracleParityError("request Host does not match the controlled endpoint")
    headers = list(request.headers)
    headers[host_index] = (headers[host_index][0], "<dynamic-loopback-authority>")
    return HTTPRequestCapture(
        request.method, request.raw_target, request.version, tuple(headers)
    )


def compare_provider_requests(
    official: HTTPRequestCapture,
    helper: HTTPRequestCapture,
    *,
    authority: str,
    expected_target: str,
    expected_user_agent: str,
) -> HTTPRequestCapture:
    normalized_official = normalize_request(official, authority=authority)
    normalized_helper = normalize_request(helper, authority=authority)
    if normalized_official != normalized_helper:
        raise OracleParityError(
            "helper HTTP/1.1 provider request differs from official Mihomo:\n"
            + json.dumps(
                {
                    "official": normalized_official.to_dict(),
                    "helper": normalized_helper.to_dict(),
                },
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
        )
    if normalized_official.method != "GET":
        raise OracleParityError("provider request method is not GET")
    if normalized_official.raw_target != expected_target:
        raise OracleParityError("provider request target changed")
    if normalized_official.version != "HTTP/1.1":
        raise OracleParityError("plaintext oracle did not use HTTP/1.1")
    user_agents = [
        value
        for name, value in normalized_official.headers
        if name.lower() == "user-agent"
    ]
    if user_agents != [expected_user_agent]:
        raise OracleParityError(
            "provider User-Agent is not exactly {}".format(expected_user_agent)
        )
    return normalized_official


def validate_locked_oracle(lock: dict[str, Any]) -> dict[str, Any]:
    mihomo = lock.get("mihomo")
    if not isinstance(mihomo, dict):
        raise OracleParityError("source lock lacks Mihomo identity")
    tag = mihomo.get("tag")
    if not isinstance(tag, str) or STABLE_TAG.fullmatch(tag) is None:
        raise OracleParityError("locked Mihomo tag is not stable semver")
    if mihomo.get("repository") != "MetaCubeX/mihomo":
        raise OracleParityError("locked Mihomo repository is not official")
    if mihomo.get("release_url") != (
        "https://github.com/MetaCubeX/mihomo/releases/tag/" + tag
    ):
        raise OracleParityError("locked Mihomo release URL is inconsistent")
    if not isinstance(mihomo.get("release_id"), int) or mihomo["release_id"] <= 0:
        raise OracleParityError("locked Mihomo release id is invalid")
    if mihomo.get("oracle_profile") != "published-platforms-v1":
        raise OracleParityError("locked Mihomo oracle profile is unsupported")
    try:
        asset = mihomo["oracle_assets"]["linux-amd64"]
    except (KeyError, TypeError) as error:
        raise OracleParityError("source lock lacks the linux-amd64 oracle") from error
    expected_name = "mihomo-linux-amd64-v1-{}.gz".format(tag)
    expected_url = (
        "https://github.com/MetaCubeX/mihomo/releases/download/{}/{}".format(
            tag, expected_name
        )
    )
    if asset.get("name") != expected_name or asset.get("download_url") != expected_url:
        raise OracleParityError("linux-amd64 oracle does not match the locked release")
    return asset


def validate_helper_hello(
    hello: Any, lock: dict[str, Any], *, expected_go_version: str | None = None
) -> None:
    if not isinstance(hello, dict):
        raise OracleParityError("helper first frame is not a CBOR map")
    expected_keys = {
        "type",
        "protocol",
        "mihomo_version",
        "mihomo_commit",
        "overlay_sha256",
        "go_version",
        "goos",
        "goarch",
        "default_user_agent",
        "capabilities",
    }
    if set(hello) != expected_keys:
        raise OracleParityError("helper hello fields do not match protocol v1")
    mihomo = lock["mihomo"]
    project = lock["project"]
    expected = {
        "type": "hello",
        "protocol": project["helper_protocol"],
        "mihomo_version": mihomo["tag"],
        "mihomo_commit": mihomo["tag_identity"]["commit"],
        "overlay_sha256": project["helper_overlay_sha256"],
        "goos": "linux",
        "goarch": "amd64",
        "default_user_agent": "clash.meta/" + mihomo["tag"],
        "capabilities": EXPECTED_CAPABILITIES,
    }
    mismatches = [key for key, value in expected.items() if hello.get(key) != value]
    if mismatches:
        raise OracleParityError(
            "helper hello identity mismatch: " + ", ".join(sorted(mismatches))
        )
    if not isinstance(hello["go_version"], str) or re.fullmatch(
        r"go[0-9]+\.[0-9]+(?:\.[0-9]+)?", hello["go_version"]
    ) is None:
        raise OracleParityError("helper hello contains an invalid Go version")
    if expected_go_version is not None and hello["go_version"] != expected_go_version:
        raise OracleParityError(
            "helper Go toolchain differs from the official Mihomo oracle"
        )


def validate_lock_contender(
    *, returncode: int, stdout: bytes, stderr: bytes
) -> None:
    if returncode == 0:
        raise OracleParityError("second helper acquired an already-locked data dir")
    if stdout != b"":
        raise OracleParityError("lock-contending helper polluted framed stdout")
    if not stderr.strip():
        raise OracleParityError("lock-contending helper did not report its error on stderr")


class RawHTTPEndpoint:
    def __init__(self, timeout: float):
        self.timeout = timeout
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.listener.settimeout(timeout)
        self.host, self.port = self.listener.getsockname()
        self.authority = "{}:{}".format(self.host, self.port)
        self.url = "http://{}/subscription?token=oracle-parity".format(
            self.authority
        )

    def close(self) -> None:
        self.listener.close()

    def capture(self, label: str) -> HTTPRequestCapture:
        try:
            connection, peer = self.listener.accept()
        except socket.timeout as error:
            raise OracleParityError("{} made no provider request".format(label)) from error
        if peer[0] != "127.0.0.1":
            connection.close()
            raise OracleParityError("{} request did not originate locally".format(label))
        with connection:
            connection.settimeout(self.timeout)
            received = bytearray()
            while b"\r\n\r\n" not in received:
                if len(received) >= MAX_HTTP_HEAD:
                    raise OracleParityError("{} HTTP headers exceed limit".format(label))
                try:
                    chunk = connection.recv(4096)
                except socket.timeout as error:
                    raise OracleParityError(
                        "{} HTTP request headers timed out".format(label)
                    ) from error
                if not chunk:
                    raise OracleParityError(
                        "{} closed before sending complete headers".format(label)
                    )
                received.extend(chunk)
            raw_head, delimiter, trailing = bytes(received).partition(b"\r\n\r\n")
            if trailing:
                raise OracleParityError("{} sent an unexpected request body".format(label))
            response = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/yaml\r\n"
                b"Content-Length: "
                + str(len(PROVIDER_BODY)).encode("ascii")
                + b"\r\nConnection: close\r\n\r\n"
                + PROVIDER_BODY
            )
            connection.sendall(response)
        return parse_http_request(raw_head + delimiter)

    def assert_no_pending_request(self) -> None:
        ready, _, _ = select.select([self.listener], [], [], QUIET_PERIOD_SECONDS)
        if ready:
            raise OracleParityError("official Mihomo emitted more than one provider request")

    def __enter__(self) -> "RawHTTPEndpoint":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


def _recv_exact_socket(connection: socket.socket, length: int, label: str) -> bytes:
    received = bytearray()
    while len(received) < length:
        try:
            chunk = connection.recv(length - len(received))
        except socket.timeout as error:
            raise OracleParityError("{} TLS capture timed out".format(label)) from error
        if not chunk:
            raise OracleParityError(
                "{} closed during an incomplete TLS capture".format(label)
            )
        received.extend(chunk)
    return bytes(received)


class TLSClientHelloEndpoint:
    def __init__(self, timeout: float):
        self.timeout = timeout
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.listener.settimeout(timeout)
        self.host, self.port = self.listener.getsockname()
        self.authority = "{}:{}".format(self.host, self.port)
        self.url = "https://{}/subscription?token=tls-oracle-parity".format(
            self.authority
        )
        self.active_connections: list[socket.socket] = []

    def close(self) -> None:
        self.release_connections()
        self.listener.close()

    def release_connections(self) -> None:
        while self.active_connections:
            connection = self.active_connections.pop()
            try:
                connection.close()
            except OSError:
                pass

    def capture(self, label: str) -> TLSClientHelloCapture:
        try:
            connection, peer = self.listener.accept()
        except socket.timeout as error:
            raise OracleParityError(
                "{} made no TLS provider connection".format(label)
            ) from error
        if peer[0] != "127.0.0.1":
            connection.close()
            raise OracleParityError(
                "{} TLS connection did not originate locally".format(label)
            )

        try:
            records: list[tuple[int, bytes]] = []
            handshake = bytearray()
            connection.settimeout(self.timeout)
            while True:
                if len(records) >= 16:
                    raise OracleParityError(
                        "{} fragmented ClientHello across too many records".format(label)
                    )
                record_header = _recv_exact_socket(connection, 5, label)
                content_type, record_version, record_length = struct.unpack(
                    ">BHH", record_header
                )
                if content_type != 22:
                    raise OracleParityError(
                        "{} sent a non-handshake TLS record before ClientHello".format(
                            label
                        )
                    )
                if not 0x0300 <= record_version <= 0x0303:
                    raise OracleParityError(
                        "{} sent an invalid TLS record version".format(label)
                    )
                if record_length <= 0 or record_length > MAX_TLS_RECORD:
                    raise OracleParityError(
                        "{} sent an invalid TLS record length".format(label)
                    )
                payload = _recv_exact_socket(connection, record_length, label)
                records.append((record_version, payload))
                handshake.extend(payload)
                if len(handshake) < 4:
                    continue
                if handshake[0] != 1:
                    raise OracleParityError(
                        "{} TLS handshake did not start with ClientHello".format(label)
                    )
                handshake_length = int.from_bytes(handshake[1:4], "big")
                if handshake_length <= 0 or handshake_length > MAX_CLIENT_HELLO:
                    raise OracleParityError(
                        "{} TLS ClientHello length is invalid".format(label)
                    )
                expected_length = 4 + handshake_length
                if len(handshake) > expected_length:
                    raise OracleParityError(
                        "{} sent trailing handshake bytes with ClientHello".format(label)
                    )
                if len(handshake) < expected_length:
                    continue

                capture = parse_tls_client_hello_records(
                    records,
                    destination_host=self.host,
                    destination_port=self.port,
                )
                # Keep the socket open and silent until the caller stops the
                # client. This captures the complete ClientHello before any
                # handshake failure and prevents retry traffic from replacing
                # the first attempt.
                self.active_connections.append(connection)
                return capture
        except Exception:
            connection.close()
            raise

    def assert_no_pending_request(self, label: str) -> None:
        ready, _, _ = select.select([self.listener], [], [], QUIET_PERIOD_SECONDS)
        if ready:
            raise OracleParityError(
                "{} emitted more than one TLS provider connection".format(label)
            )

    def __enter__(self) -> "TLSClientHelloEndpoint":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _read_log(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "<unavailable>"
    return content[-4000:]


def parse_official_version_output(output: str, tag: str) -> tuple[str, str]:
    lines = output.strip().splitlines()
    if not lines:
        raise OracleParityError("official Mihomo version output is empty")
    first_line = lines[0]
    match = re.fullmatch(
        r"Mihomo Meta {} linux amd64 with (go[0-9]+\.[0-9]+\.[0-9]+)(?: .+)?".format(
            re.escape(tag)
        ),
        first_line,
    )
    if match is None:
        raise OracleParityError("official Mihomo binary identity does not match source lock")
    return first_line, match.group(1)


def verify_official_version(
    binary: Path, tag: str, timeout: float
) -> tuple[str, str]:
    try:
        result = subprocess.run(
            [str(binary), "-v"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OracleParityError("official Mihomo version probe failed") from error
    output = result.stdout.decode("utf-8", errors="replace")
    if result.returncode != 0:
        raise OracleParityError("official Mihomo binary identity does not match source lock")
    return parse_official_version_output(output, tag)


def _write_official_config(path: Path, provider_url: str) -> None:
    path.write_text(
        (
            "mixed-port: 0\n"
            "allow-lan: false\n"
            "ipv6: false\n"
            "mode: rule\n"
            "log-level: debug\n"
            "proxy-providers:\n"
            "  oracle:\n"
            "    type: http\n"
            "    url: {}\n"
            "    interval: 3600\n"
            "    path: ./providers/oracle.yaml\n"
            "    health-check:\n"
            "      enable: false\n"
            "proxy-groups:\n"
            "  - name: ORACLE\n"
            "    type: select\n"
            "    use:\n"
            "      - oracle\n"
            "rules:\n"
            "  - MATCH,ORACLE\n"
        ).format(json.dumps(provider_url)),
        encoding="utf-8",
        newline="\n",
    )


def capture_official_request(
    binary: Path,
    tag: str,
    endpoint: RawHTTPEndpoint,
    work: Path,
    timeout: float,
) -> tuple[HTTPRequestCapture, str, str]:
    version, go_version = verify_official_version(binary, tag, timeout)
    runtime = work / "official-runtime"
    runtime.mkdir()
    config = work / "official-config.yaml"
    _write_official_config(config, endpoint.url)
    log = work / "official.log"
    with log.open("wb") as output:
        try:
            process = subprocess.Popen(
                [str(binary), "-d", str(runtime), "-f", str(config)],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        except OSError as error:
            raise OracleParityError("unable to start official Mihomo") from error
        try:
            request = endpoint.capture("official Mihomo")
            try:
                process.wait(timeout=QUIET_PERIOD_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            else:
                raise OracleParityError(
                    "official Mihomo exited after provider fetch:\n" + _read_log(log)
                )
            return request, version, go_version
        except Exception as error:
            if isinstance(error, OracleParityError):
                raise OracleParityError(
                    "{}\nofficial Mihomo log:\n{}".format(error, _read_log(log))
                ) from error
            raise
        finally:
            _stop_process(process)


def capture_official_tls_client_hello(
    binary: Path,
    tag: str,
    endpoint: TLSClientHelloEndpoint,
    work: Path,
    timeout: float,
) -> tuple[TLSClientHelloCapture, str, str]:
    version, go_version = verify_official_version(binary, tag, timeout)
    runtime = work / "official-tls-runtime"
    runtime.mkdir()
    config = work / "official-tls-config.yaml"
    _write_official_config(config, endpoint.url)
    log = work / "official-tls.log"
    with log.open("wb") as output:
        try:
            process = subprocess.Popen(
                [str(binary), "-d", str(runtime), "-f", str(config)],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        except OSError as error:
            raise OracleParityError("unable to start official Mihomo for TLS") from error
        try:
            capture = endpoint.capture("official Mihomo")
            try:
                process.wait(timeout=QUIET_PERIOD_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            else:
                raise OracleParityError(
                    "official Mihomo exited after TLS capture:\n" + _read_log(log)
                )
            return capture, version, go_version
        except Exception as error:
            if isinstance(error, OracleParityError):
                raise OracleParityError(
                    "{}\nofficial Mihomo TLS log:\n{}".format(error, _read_log(log))
                ) from error
            raise
        finally:
            _stop_process(process)
            endpoint.release_connections()


def assert_helper_stdout_quiet(process: subprocess.Popen[bytes]) -> None:
    if process.stdout is None:
        raise OracleParityError("helper stdout pipe is unavailable")
    ready, _, _ = select.select(
        [process.stdout.fileno()], [], [], QUIET_PERIOD_SECONDS
    )
    if ready:
        unexpected = os.read(process.stdout.fileno(), 1)
        if unexpected:
            raise OracleParityError("helper emitted an unsolicited second frame or log")
    if process.poll() is not None:
        raise OracleParityError("helper exited unexpectedly after hello")


def assert_data_dir_lock(
    helper: Path, environment: dict[str, str], timeout: float
) -> None:
    try:
        contender = subprocess.run(
            [str(helper)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OracleParityError(
            "second helper did not fail promptly on the occupied data dir"
        ) from error
    validate_lock_contender(
        returncode=contender.returncode,
        stdout=contender.stdout,
        stderr=contender.stderr,
    )


def validate_fetch_response(response: Any, *, url: str) -> None:
    if not isinstance(response, dict):
        raise OracleParityError("helper fetch response is not a CBOR map")
    if response.get("type") != "response" or response.get("id") != 1:
        raise OracleParityError("helper fetch response envelope is invalid")
    if response.get("error_code") or response.get("error_message"):
        raise OracleParityError("helper reported a provider fetch error")
    if response.get("status") != 200:
        raise OracleParityError("helper provider fetch did not return HTTP 200")
    if response.get("final_url") != url:
        raise OracleParityError("helper final URL differs from the controlled URL")
    if response.get("body") != PROVIDER_BODY:
        raise OracleParityError("helper did not return the controlled provider body")


def capture_helper_request(
    helper: Path,
    lock: dict[str, Any],
    endpoint: RawHTTPEndpoint,
    work: Path,
    timeout: float,
    expected_go_version: str,
) -> tuple[HTTPRequestCapture, dict[str, Any]]:
    data_dir = work / "shared-helper-data"
    environment = os.environ.copy()
    environment["SUBCONVERTER_MIHOMO_DATA_DIR"] = str(data_dir)
    log = work / "helper.log"
    with log.open("wb") as error_output:
        try:
            process = subprocess.Popen(
                [str(helper)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=error_output,
                bufsize=0,
                env=environment,
            )
        except OSError as error:
            raise OracleParityError("unable to start locked Mihomo helper") from error
        try:
            if process.stdin is None or process.stdout is None:
                raise OracleParityError("helper IPC pipes are unavailable")
            hello = read_frame(process.stdout, timeout)
            validate_helper_hello(
                hello, lock, expected_go_version=expected_go_version
            )
            assert_helper_stdout_quiet(process)
            assert_data_dir_lock(helper, environment, timeout)
            write_frame(
                process.stdin,
                {
                    "type": "fetch",
                    "id": 1,
                    "url": endpoint.url,
                    "headers": {},
                    "proxy": "",
                    "old_hash": "",
                    "timeout_ms": int(timeout * 1000),
                    "size_limit": 1024 * 1024,
                },
            )
            request = endpoint.capture("locked helper")
            response = read_frame(process.stdout, timeout)
            validate_fetch_response(response, url=endpoint.url)
            assert_helper_stdout_quiet(process)
            return request, hello
        except Exception as error:
            if isinstance(error, OracleParityError):
                raise OracleParityError(
                    "{}\nhelper stderr:\n{}".format(error, _read_log(log))
                ) from error
            raise
        finally:
            _stop_process(process)


def capture_helper_tls_client_hello(
    helper: Path,
    lock: dict[str, Any],
    endpoint: TLSClientHelloEndpoint,
    work: Path,
    timeout: float,
    expected_go_version: str,
) -> tuple[TLSClientHelloCapture, dict[str, Any]]:
    data_dir = work / "tls-helper-data"
    environment = os.environ.copy()
    environment["SUBCONVERTER_MIHOMO_DATA_DIR"] = str(data_dir)
    log = work / "tls-helper.log"
    with log.open("wb") as error_output:
        try:
            process = subprocess.Popen(
                [str(helper)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=error_output,
                bufsize=0,
                env=environment,
            )
        except OSError as error:
            raise OracleParityError("unable to start locked Mihomo helper for TLS") from error
        try:
            if process.stdin is None or process.stdout is None:
                raise OracleParityError("helper TLS IPC pipes are unavailable")
            hello = read_frame(process.stdout, timeout)
            validate_helper_hello(
                hello, lock, expected_go_version=expected_go_version
            )
            assert_helper_stdout_quiet(process)
            write_frame(
                process.stdin,
                {
                    "type": "fetch",
                    "id": 1,
                    "url": endpoint.url,
                    "headers": {},
                    "proxy": "",
                    "old_hash": "",
                    "timeout_ms": int(timeout * 1000),
                    "size_limit": 1024 * 1024,
                },
            )
            capture = endpoint.capture("locked helper")
            assert_helper_stdout_quiet(process)
            return capture, hello
        except Exception as error:
            if isinstance(error, OracleParityError):
                raise OracleParityError(
                    "{}\nhelper TLS stderr:\n{}".format(error, _read_log(log))
                ) from error
            raise
        finally:
            _stop_process(process)
            endpoint.release_connections()


def prepare_official_binary(
    lock: dict[str, Any], cache_dir: Path, destination: Path
) -> tuple[Path, dict[str, Any]]:
    asset = validate_locked_oracle(lock)
    archive = _download_asset(asset, cache_dir, "locked linux-amd64 Mihomo oracle")
    destination.write_bytes(_oracle_executable(archive, asset))
    destination.chmod(0o755)
    _validate_binary_platform(destination, "linux-amd64")
    return destination, asset


def install_verified_helper(
    lock_path: Path, helper: Path, manifest: Path, work: Path
) -> Path:
    destination = work / "verified-helper" / "subconverter-mihomo-fetcher"
    manifest_destination = work / "verified-helper" / "manifest.json"
    install_locked(
        Namespace(
            lock=str(lock_path),
            platform="linux-amd64",
            binary=str(helper),
            manifest=str(manifest),
            destination=str(destination),
            manifest_destination=str(manifest_destination),
        )
    )
    return destination


def run_parity(args: argparse.Namespace) -> dict[str, Any]:
    if sys.platform != "linux" or platform.machine().lower() not in {
        "x86_64",
        "amd64",
    }:
        raise OracleParityError("oracle parity requires a Linux amd64 host")
    if not 1 <= args.timeout_seconds <= 120:
        raise OracleParityError("timeout must be between 1 and 120 seconds")

    lock_path = Path(args.lock).resolve()
    helper = Path(args.helper).resolve()
    manifest = Path(args.helper_manifest).resolve()
    cache_dir = Path(args.cache_dir).resolve()
    lock = load_and_validate_lock(lock_path)
    tag = lock["mihomo"]["tag"]

    with tempfile.TemporaryDirectory(prefix="subconverter-mihomo-parity-") as directory:
        work = Path(directory)
        official, asset = prepare_official_binary(
            lock, cache_dir, work / "official-mihomo"
        )
        verified_helper = install_verified_helper(
            lock_path, helper, manifest, work
        )
        with RawHTTPEndpoint(args.timeout_seconds) as endpoint:
            official_request, official_version, official_go_version = capture_official_request(
                official, tag, endpoint, work, args.timeout_seconds
            )
            endpoint.assert_no_pending_request()
            helper_request, hello = capture_helper_request(
                verified_helper,
                lock,
                endpoint,
                work,
                args.timeout_seconds,
                official_go_version,
            )
            parsed_url = urllib.parse.urlsplit(endpoint.url)
            expected_target = parsed_url.path
            if parsed_url.query:
                expected_target += "?" + parsed_url.query
            normalized = compare_provider_requests(
                official_request,
                helper_request,
                authority=endpoint.authority,
                expected_target=expected_target,
                expected_user_agent="clash.meta/" + tag,
            )
        with TLSClientHelloEndpoint(args.timeout_seconds) as tls_endpoint:
            (
                official_tls,
                tls_official_version,
                tls_official_go_version,
            ) = capture_official_tls_client_hello(
                official, tag, tls_endpoint, work, args.timeout_seconds
            )
            if (
                tls_official_version != official_version
                or tls_official_go_version != official_go_version
            ):
                raise OracleParityError(
                    "official Mihomo identity changed between HTTP and TLS captures"
                )
            tls_endpoint.assert_no_pending_request("official Mihomo")
            helper_tls, tls_hello = capture_helper_tls_client_hello(
                verified_helper,
                lock,
                tls_endpoint,
                work,
                args.timeout_seconds,
                official_go_version,
            )
            tls_endpoint.assert_no_pending_request("locked helper")
            if tls_hello != hello:
                raise OracleParityError(
                    "helper identity changed between HTTP and TLS captures"
                )
            normalized_tls = compare_tls_client_hellos(official_tls, helper_tls)

    return {
        "result": "pass",
        "profile": "provider_fetch_http1_and_tls_client_hello",
        "scope": [
            "request-line-http-version-ordered-headers",
            "tls-record-and-client-hello",
        ],
        "not_tested": ["HTTP/2"],
        "limitations": [
            "TLS handshake intentionally stops after ClientHello; certificates are not exchanged"
        ],
        "mihomo": {
            "tag": tag,
            "commit": lock["mihomo"]["tag_identity"]["commit"],
            "official_archive": asset["name"],
            "official_archive_sha256": asset["digest"],
            "version_output": official_version,
            "go_version": official_go_version,
        },
        "helper": {
            "protocol": hello["protocol"],
            "overlay_sha256": hello["overlay_sha256"],
            "default_user_agent": hello["default_user_agent"],
            "go_version": hello["go_version"],
            "exclusive_data_dir_lock": True,
            "framed_stdout_clean": True,
        },
        "normalized_request": normalized.to_dict(),
        "tls_client_hello": {
            "result": "pass",
            "normalized": normalized_tls,
        },
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--lock", default=str(DEFAULT_LOCK))
    root.add_argument("--helper", required=True)
    root.add_argument("--helper-manifest", required=True)
    root.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    root.add_argument("--timeout-seconds", type=float, default=20.0)
    return root


def main(argv: list[str] | None = None) -> int:
    try:
        result = run_parity(parser().parse_args(argv))
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        return 0
    except (OracleParityError, PackagingError, OSError, ValueError) as error:
        print("Mihomo provider oracle parity failed: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
