import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "test_mihomo_oracle_parity.py"
SPEC = importlib.util.spec_from_file_location("test_mihomo_oracle_parity_script", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


TAG = "v1.19.29"
COMMIT = "e26714a181ac0e2fa803453c0a8e9a9ce94e31cb"
OVERLAY = "sha256:" + "1" * 64
AUTHORITY = "127.0.0.1:43210"
TARGET = "/subscription?token=oracle-parity"


def source_lock():
    name = "mihomo-linux-amd64-v1-{}.gz".format(TAG)
    return {
        "mihomo": {
            "repository": "MetaCubeX/mihomo",
            "tag": TAG,
            "tag_identity": {"commit": COMMIT},
            "release_id": 123,
            "release_url": "https://github.com/MetaCubeX/mihomo/releases/tag/" + TAG,
            "oracle_profile": "published-platforms-v1",
            "oracle_assets": {
                "linux-amd64": {
                    "name": name,
                    "download_url": (
                        "https://github.com/MetaCubeX/mihomo/releases/download/"
                        + TAG
                        + "/"
                        + name
                    ),
                    "digest": "sha256:" + "2" * 64,
                    "id": 456,
                    "size": 789,
                }
            },
        },
        "project": {
            "helper_protocol": 1,
            "helper_overlay_sha256": OVERLAY,
        },
    }


def helper_hello():
    return {
        "type": "hello",
        "protocol": 1,
        "mihomo_version": TAG,
        "mihomo_commit": COMMIT,
        "overlay_sha256": OVERLAY,
        "go_version": "go1.26.5",
        "goos": "linux",
        "goarch": "amd64",
        "default_user_agent": "clash.meta/" + TAG,
        "capabilities": list(MODULE.EXPECTED_CAPABILITIES),
    }


def request(headers=None):
    if headers is None:
        headers = (
            ("Host", AUTHORITY),
            ("User-Agent", "clash.meta/" + TAG),
            ("Accept-Encoding", "gzip"),
        )
    return MODULE.HTTPRequestCapture("GET", TARGET, "HTTP/1.1", tuple(headers))


def tls_extension(extension_type, payload):
    return (
        extension_type.to_bytes(2, "big")
        + len(payload).to_bytes(2, "big")
        + payload
    )


def uint16_vector(values):
    payload = b"".join(value.to_bytes(2, "big") for value in values)
    return len(payload).to_bytes(2, "big") + payload


def make_tls_capture(
    *,
    random_byte=0x11,
    session_byte=0x22,
    key_byte=0x33,
    destination_port=44301,
    cipher_suites=(0x1301, 0x1302, 0xC02F),
    extension_order=(10, 13, 16, 43, 51),
    supported_groups=(0x001D, 0x0017),
    signature_algorithms=(0x0804, 0x0403),
    alpn=(b"h2", b"http/1.1"),
):
    alpn_payload = b"".join(bytes([len(item)]) + item for item in alpn)
    key_exchange = bytes([key_byte]) * 32
    key_share = (
        (36).to_bytes(2, "big")
        + (0x001D).to_bytes(2, "big")
        + len(key_exchange).to_bytes(2, "big")
        + key_exchange
    )
    extension_payloads = {
        10: uint16_vector(supported_groups),
        13: uint16_vector(signature_algorithms),
        16: len(alpn_payload).to_bytes(2, "big") + alpn_payload,
        43: b"\x04\x03\x04\x03\x03",
        51: key_share,
    }
    extensions = b"".join(
        tls_extension(extension_type, extension_payloads[extension_type])
        for extension_type in extension_order
    )
    session_id = bytes([session_byte]) * 32
    body = (
        b"\x03\x03"
        + bytes([random_byte]) * 32
        + bytes([len(session_id)])
        + session_id
        + uint16_vector(cipher_suites)
        + b"\x01\x00"
        + len(extensions).to_bytes(2, "big")
        + extensions
    )
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return MODULE.parse_tls_client_hello_records(
        [(0x0301, handshake)],
        destination_host="127.0.0.1",
        destination_port=destination_port,
    )


class MihomoOracleParityTests(unittest.TestCase):
    def test_minimal_cbor_codec_round_trips_protocol_values(self):
        value = {
            "type": "response",
            "id": 1,
            "status": 200,
            "headers": {"Content-Type": ["text/yaml"]},
            "body": b"provider",
            "not_modified": False,
            "optional": None,
        }
        self.assertEqual(MODULE.decode_cbor(MODULE.encode_cbor(value)), value)

    def test_minimal_cbor_codec_rejects_trailing_and_duplicate_keys(self):
        with self.assertRaisesRegex(MODULE.OracleParityError, "trailing"):
            MODULE.decode_cbor(MODULE.encode_cbor({"id": 1}) + b"\x00")
        duplicate_map = b"\xa2\x61a\x01\x61a\x02"
        with self.assertRaisesRegex(MODULE.OracleParityError, "duplicate"):
            MODULE.decode_cbor(duplicate_map)

    def test_raw_http_parser_preserves_request_and_header_order(self):
        parsed = MODULE.parse_http_request(
            (
                "GET {} HTTP/1.1\r\n"
                "Host: {}\r\n"
                "User-Agent: clash.meta/{}\r\n"
                "Accept-Encoding: gzip\r\n\r\n"
            ).format(TARGET, AUTHORITY, TAG).encode("ascii")
        )
        self.assertEqual(parsed, request())

    def test_comparison_masks_only_the_controlled_host_authority(self):
        normalized = MODULE.compare_provider_requests(
            request(),
            request(),
            authority=AUTHORITY,
            expected_target=TARGET,
            expected_user_agent="clash.meta/" + TAG,
        )
        self.assertEqual(
            normalized.headers[0], ("Host", "<dynamic-loopback-authority>")
        )
        self.assertEqual(normalized.headers[1:], request().headers[1:])

    def test_comparison_rejects_header_order_name_and_value_drift(self):
        candidates = (
            request((request().headers[1], request().headers[0], request().headers[2])),
            request(
                (
                    ("host", AUTHORITY),
                    request().headers[1],
                    request().headers[2],
                )
            ),
            request(
                (
                    request().headers[0],
                    ("User-Agent", "mihomo/" + TAG),
                    request().headers[2],
                )
            ),
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                with self.assertRaises(MODULE.OracleParityError):
                    MODULE.compare_provider_requests(
                        request(),
                        candidate,
                        authority=AUTHORITY,
                        expected_target=TARGET,
                        expected_user_agent="clash.meta/" + TAG,
                    )

    def test_locked_oracle_is_bound_to_official_release_identity(self):
        lock = source_lock()
        asset = MODULE.validate_locked_oracle(lock)
        self.assertEqual(asset["name"], "mihomo-linux-amd64-v1-" + TAG + ".gz")

        lock = source_lock()
        lock["mihomo"]["oracle_assets"]["linux-amd64"]["download_url"] = (
            "https://example.invalid/oracle.gz"
        )
        with self.assertRaisesRegex(MODULE.OracleParityError, "locked release"):
            MODULE.validate_locked_oracle(lock)

    def test_helper_first_frame_requires_exact_locked_identity(self):
        MODULE.validate_helper_hello(
            helper_hello(), source_lock(), expected_go_version="go1.26.5"
        )
        for field in ("type", "mihomo_commit", "default_user_agent", "capabilities"):
            with self.subTest(field=field):
                hello = helper_hello()
                hello[field] = "wrong"
                with self.assertRaises(MODULE.OracleParityError):
                    MODULE.validate_helper_hello(hello, source_lock())

        hello = helper_hello()
        hello["unexpected"] = True
        with self.assertRaisesRegex(MODULE.OracleParityError, "fields"):
            MODULE.validate_helper_hello(hello, source_lock())

        with self.assertRaisesRegex(MODULE.OracleParityError, "toolchain"):
            MODULE.validate_helper_hello(
                helper_hello(), source_lock(), expected_go_version="go1.26.6"
            )

    def test_official_version_strictly_exposes_the_go_toolchain(self):
        output = (
            "Mihomo Meta v1.19.29 linux amd64 with go1.26.5 "
            "Sat Jul 18 12:20:03 UTC 2026\nUse tags: with_gvisor\n"
        )
        first_line, go_version = MODULE.parse_official_version_output(output, TAG)
        self.assertEqual(go_version, "go1.26.5")
        self.assertTrue(first_line.startswith("Mihomo Meta " + TAG))

        for invalid in (
            output.replace("go1.26.5", "go1.26"),
            output.replace(TAG, "v1.19.28"),
            "prefix " + output,
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(MODULE.OracleParityError):
                    MODULE.parse_official_version_output(invalid, TAG)

    def test_lock_contender_must_fail_without_stdout_pollution(self):
        MODULE.validate_lock_contender(
            returncode=1, stdout=b"", stderr=b"cache lock unavailable\n"
        )
        invalid = (
            {"returncode": 0, "stdout": b"", "stderr": b""},
            {"returncode": 1, "stdout": b"log on stdout", "stderr": b"error"},
            {"returncode": 1, "stdout": b"", "stderr": b""},
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(MODULE.OracleParityError):
                    MODULE.validate_lock_contender(**arguments)

    def test_fetch_response_must_be_complete_and_bound_to_url(self):
        response = {
            "type": "response",
            "id": 1,
            "status": 200,
            "final_url": "http://127.0.0.1/subscription",
            "body": MODULE.PROVIDER_BODY,
        }
        MODULE.validate_fetch_response(
            response, url="http://127.0.0.1/subscription"
        )
        response["status"] = 0
        with self.assertRaises(MODULE.OracleParityError):
            MODULE.validate_fetch_response(
                response, url="http://127.0.0.1/subscription"
            )

    def test_tls_normalization_masks_only_documented_ephemeral_fields(self):
        official = make_tls_capture()
        helper = make_tls_capture(
            random_byte=0xAA,
            session_byte=0xBB,
            key_byte=0xCC,
            destination_port=44302,
        )
        normalized = MODULE.compare_tls_client_hellos(official, helper)
        self.assertEqual(normalized["random"], "<client-random:32-bytes>")
        self.assertEqual(normalized["session_id"], "<session-id:32-bytes>")
        self.assertEqual(normalized["destination_port"], "<dynamic-port>")
        key_share = [
            extension
            for extension in normalized["extensions"]
            if extension["type"] == 51
        ][0]
        self.assertEqual(
            key_share["decoded"][0]["key_exchange"], "<ephemeral-bytes:32>"
        )

    def test_tls_comparison_rejects_order_and_non_ephemeral_content_drift(self):
        official = make_tls_capture()
        candidates = (
            make_tls_capture(cipher_suites=(0x1302, 0x1301, 0xC02F)),
            make_tls_capture(extension_order=(13, 10, 16, 43, 51)),
            make_tls_capture(supported_groups=(0x0017, 0x001D)),
            make_tls_capture(signature_algorithms=(0x0403, 0x0804)),
            make_tls_capture(alpn=(b"http/1.1", b"h2")),
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                with self.assertRaises(MODULE.OracleParityError):
                    MODULE.compare_tls_client_hellos(official, candidate)

    def test_tls_parser_fails_closed_on_incomplete_or_malformed_capture(self):
        valid = make_tls_capture()
        self.assertEqual(valid.legacy_version, 0x0303)
        with self.assertRaisesRegex(MODULE.OracleParityError, "incomplete"):
            MODULE.parse_tls_client_hello_records(
                [(0x0301, b"\x01\x00\x00\x20" + b"\x03\x03")],
                destination_host="127.0.0.1",
                destination_port=443,
            )

        malformed_groups = make_tls_capture().extensions
        mutated = tuple(
            (extension_type, b"\x00\x03\x00\x1d\x00")
            if extension_type == 10
            else (extension_type, payload)
            for extension_type, payload in malformed_groups
        )
        capture = MODULE.TLSClientHelloCapture(
            destination_host=valid.destination_host,
            destination_port=valid.destination_port,
            record_versions=valid.record_versions,
            record_payload_lengths=valid.record_payload_lengths,
            legacy_version=valid.legacy_version,
            random=valid.random,
            session_id=valid.session_id,
            cipher_suites=valid.cipher_suites,
            compression_methods=valid.compression_methods,
            extensions=mutated,
        )
        with self.assertRaises(MODULE.OracleParityError):
            MODULE.normalize_tls_client_hello(capture)


if __name__ == "__main__":
    unittest.main()
