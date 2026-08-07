import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.mihomo_conformance import (
    CaptureValidationError,
    OfficialAssetValidationError,
    compare_captures,
    load_capture_schema,
    load_normalization_policy,
    normalize_capture,
    validate_capture,
    validate_capture_collection,
    validate_official_asset_metadata,
    validate_reference_capture_subject,
    verify_official_asset_file,
)
from scripts.mihomo_conformance.policy import PolicyValidationError


EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
BODY = b"c3M6Ly9ZV1Z6TFRFeU9DMW5ZMjA2Y0dGemMzZHZjbVE9QDEyNy4wLjAuMTo4Mzg4"
BODY_SHA256 = hashlib.sha256(BODY).hexdigest()
OFFICIAL_BYTES = b"synthetic official Mihomo release asset"
OFFICIAL_SHA256 = hashlib.sha256(OFFICIAL_BYTES).hexdigest()
TAG = "v1.19.29"
ASSET_NAME = "mihomo-linux-amd64-compatible-v1.19.29.gz"


def release_metadata(*, digest=OFFICIAL_SHA256, draft=False, prerelease=False):
    release_id = 101
    asset_id = 202
    return {
        "id": release_id,
        "url": (
            "https://api.github.com/repos/MetaCubeX/mihomo/releases/"
            f"{release_id}"
        ),
        "html_url": f"https://github.com/MetaCubeX/mihomo/releases/tag/{TAG}",
        "tag_name": TAG,
        "draft": draft,
        "prerelease": prerelease,
        "assets": [
            {
                "id": asset_id,
                "url": (
                    "https://api.github.com/repos/MetaCubeX/mihomo/"
                    f"releases/assets/{asset_id}"
                ),
                "name": ASSET_NAME,
                "state": "uploaded",
                "size": len(OFFICIAL_BYTES),
                "digest": f"sha256:{digest}",
                "browser_download_url": (
                    "https://github.com/MetaCubeX/mihomo/releases/download/"
                    f"{TAG}/{ASSET_NAME}"
                ),
            }
        ],
    }


def tls_handshake(*, candidate=False):
    return {
        "sequence": 0,
        "timestamp_ns": 20 if not candidate else 999,
        "connection_sequence": 0,
        "sni": "subscription.test",
        "client_random": ("11" if not candidate else "aa") * 32,
        "session_id": ("22" if not candidate else "bb") * 16,
        "ja3": "0123456789abcdef0123456789abcdef",
        "ja4": "t13d1516h2_8daaf6152771_02713d6af862",
        "offered_versions": ["TLS1.3", "TLS1.2"],
        "cipher_suites": [4865, 4866, 4867, 49195],
        "extensions": [0, 11, 10, 35, 16, 5, 13, 18, 51, 45, 43],
        "supported_groups": [29, 23, 24],
        "signature_algorithms": [2052, 1027, 2055],
        "key_share_groups": [29],
        "key_share_public_values": [("33" if not candidate else "cc") * 32],
        "alpn_offered": ["h2", "http/1.1"],
        "alpn_selected": "http/1.1",
        "resumed": False,
    }


def http1_request(*, candidate=False):
    return {
        "sequence": 0,
        "timestamp_ns": 30 if not candidate else 1001,
        "connection_sequence": 0,
        "method": "GET",
        "raw_target": "/subscription?token=fixed",
        "version": "HTTP/1.1",
        "headers": [
            {"name": "Host", "value": "subscription.test"},
            {"name": "User-Agent", "value": "clash.meta/v1.19.29"},
            {"name": "Accept-Encoding", "value": "gzip"},
        ],
        "body_sha256": EMPTY_SHA256,
        "body_length": 0,
    }


def http2_layers(*, candidate=False):
    return {
        "http1": {"status": "not_applicable", "reason": "http2"},
        "http2": {
            "status": "captured",
            "connections": [
                {
                    "connection_sequence": 0,
                    "settings": [
                        {"id": 2, "value": 0},
                        {"id": 4, "value": 4194304},
                    ],
                    "connection_window_update": 1073741824,
                    "priority_frames": [],
                    "goaway_frames": [],
                }
            ],
            "requests": [
                {
                    "sequence": 0,
                    "timestamp_ns": 31 if not candidate else 1002,
                    "connection_sequence": 0,
                    "stream_id": 1,
                    "pseudo_headers": [
                        {"name": ":authority", "value": "subscription.test"},
                        {"name": ":method", "value": "GET"},
                        {
                            "name": ":path",
                            "value": "/subscription?token=fixed",
                        },
                        {"name": ":scheme", "value": "https"},
                    ],
                    "headers": [
                        {
                            "name": "user-agent",
                            "value": "clash.meta/v1.19.29",
                        },
                        {"name": "accept-encoding", "value": "gzip"},
                    ],
                    "body_sha256": EMPTY_SHA256,
                    "body_length": 0,
                }
            ],
        },
    }


def capture(kind, *, profile="https_http1", candidate=False):
    subject = (
        {
            "kind": "official_mihomo",
            "name": "mihomo",
            "version": TAG,
            "artifact_sha256": OFFICIAL_SHA256,
        }
        if kind == "official_mihomo"
        else {
            "kind": "subconverter",
            "name": "subconverter",
            "version": "v0.9.9-test",
            "artifact_sha256": "cd" * 32,
        }
    )
    result = {
        "schema_version": 1,
        "profile": profile,
        "scenario_id": "default-get",
        "capture_id": (
            "00000000-0000-4000-8000-000000000002"
            if candidate
            else "00000000-0000-4000-8000-000000000001"
        ),
        "captured_at": (
            "2026-08-07T08:00:01+00:00"
            if candidate
            else "2026-08-07T08:00:00+00:00"
        ),
        "capture_complete": True,
        "collector_errors": [],
        "subject": subject,
        "environment": {
            "os": "linux",
            "architecture": "amd64",
            "network_profile": "shared-netns-v1",
            "resolver_profile": "controlled-dual-stack-v1",
            "capture_tool": "mihomo-conformance-lab",
            "capture_tool_version": "1",
        },
        "layers": {
            "dns": {
                "status": "captured",
                "queries": [
                    {
                        "sequence": 0,
                        "timestamp_ns": 10 if not candidate else 998,
                        "transaction_id": 42 if not candidate else 60000,
                        "name": "subscription.test",
                        "query_type": "A",
                        "transport": "udp",
                        "response_code": "NOERROR",
                        "answers": ["192.0.2.10"],
                    }
                ],
            },
            "tcp": {
                "status": "captured",
                "connections": [
                    {
                        "sequence": 0,
                        "timestamp_ns": 15 if not candidate else 997,
                        "ip_version": 4,
                        "source_ip": "192.0.2.20",
                        "source_port": 40000 if not candidate else 50000,
                        "destination_ip": "192.0.2.10",
                        "destination_port": 443 if profile != "http1_plaintext" else 80,
                        "outcome": "connected",
                    }
                ],
            },
            "tls": {
                "status": "captured",
                "handshakes": [tls_handshake(candidate=candidate)],
            },
            "http1": {
                "status": "captured",
                "requests": [http1_request(candidate=candidate)],
            },
            "http2": {"status": "not_applicable", "reason": "http1"},
            "application": {
                "status": "captured",
                "result": "success",
                "requests_observed": 1,
                "downloaded_body_sha256": BODY_SHA256,
                "downloaded_body_length": len(BODY),
                "elapsed_ms": 1000 if not candidate else 1450,
            },
        },
    }
    if profile == "http1_plaintext":
        result["layers"]["tls"] = {
            "status": "not_applicable",
            "reason": "plaintext",
        }
    elif profile == "https_http2":
        result["layers"].update(http2_layers(candidate=candidate))
        result["layers"]["tls"]["handshakes"][0]["alpn_selected"] = "h2"
    return result


class MihomoConformanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.asset_path = Path(self.temporary_directory.name) / ASSET_NAME
        self.asset_path.write_bytes(OFFICIAL_BYTES)
        self.identity = validate_official_asset_metadata(
            release_metadata(),
            expected_tag=TAG,
            expected_asset_name=ASSET_NAME,
            platform="linux",
            architecture="amd64",
        )
        self.verified_asset = verify_official_asset_file(
            self.identity, self.asset_path
        )

    def test_schema_and_policy_are_versioned_and_offline(self):
        schema = load_capture_schema()
        policy = load_normalization_policy()
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(policy["policy_version"], 1)
        self.assertEqual(policy["capture_schema_version"], 1)

    def test_validates_each_supported_capture_profile(self):
        for profile in ("http1_plaintext", "https_http1", "https_http2"):
            with self.subTest(profile=profile):
                validate_capture(capture("official_mihomo", profile=profile))

    def test_unknown_capture_field_fails_closed(self):
        document = capture("official_mihomo")
        document["layers"]["tls"]["handshakes"][0]["raw_client_hello"] = "00"
        with self.assertRaisesRegex(CaptureValidationError, "unknown fields"):
            validate_capture(document)

    def test_missing_captured_field_fails_closed(self):
        document = capture("official_mihomo")
        del document["layers"]["tls"]["handshakes"][0]["ja4"]
        with self.assertRaisesRegex(CaptureValidationError, "missing required fields"):
            validate_capture(document)

    def test_incomplete_collector_state_fails_closed(self):
        document = capture("official_mihomo")
        document["collector_errors"] = ["TLS parser stopped early"]
        with self.assertRaises(CaptureValidationError):
            validate_capture(document)

        document = capture("official_mihomo")
        document["capture_complete"] = False
        with self.assertRaises(CaptureValidationError):
            validate_capture(document)

    def test_not_captured_is_not_a_valid_layer_state(self):
        document = capture("official_mihomo")
        document["layers"]["tls"] = {"status": "not_captured"}
        with self.assertRaises(CaptureValidationError):
            validate_capture(document)

    def test_profile_cannot_mark_required_tls_not_applicable(self):
        document = capture("official_mihomo")
        document["layers"]["tls"] = {
            "status": "not_applicable",
            "reason": "plaintext",
        }
        with self.assertRaisesRegex(CaptureValidationError, "requires 'captured'"):
            validate_capture(document)

    def test_collection_rejects_duplicate_capture_ids(self):
        first = capture("official_mihomo")
        second = copy.deepcopy(first)
        with self.assertRaisesRegex(CaptureValidationError, "duplicate capture_id"):
            validate_capture_collection([first, second])

    def test_normalization_masks_only_audited_random_fields(self):
        reference = capture("official_mihomo")
        candidate = capture("subconverter", candidate=True)
        normalized_reference = normalize_capture(reference)
        normalized_candidate = normalize_capture(candidate)
        self.assertEqual(
            normalized_reference["layers"]["tls"]["handshakes"][0]["client_random"],
            "<masked>",
        )
        self.assertEqual(
            normalized_reference["layers"]["http1"]["requests"][0]["headers"],
            normalized_candidate["layers"]["http1"]["requests"][0]["headers"],
        )

    def test_policy_rejects_an_overbroad_mask(self):
        policy = load_normalization_policy()
        policy["masks"].append(
            {"path": "/layers/http1/requests/*/headers", "replacement": "<masked>"}
        )
        with self.assertRaisesRegex(PolicyValidationError, "audited allowlist"):
            normalize_capture(capture("official_mihomo"), policy)

    def test_structured_diff_accepts_only_masked_and_tolerated_differences(self):
        report = compare_captures(
            capture("official_mihomo"),
            capture("subconverter", candidate=True),
            official_asset=self.verified_asset,
        )
        self.assertTrue(report.equal)
        self.assertEqual(report.differences, ())
        parsed = json.loads(report.to_json())
        self.assertEqual(parsed["report_schema_version"], 1)
        self.assertTrue(parsed["equal"])

    def test_structured_diff_reports_header_value_and_path(self):
        candidate = capture("subconverter", candidate=True)
        candidate["layers"]["http1"]["requests"][0]["headers"][1][
            "value"
        ] = "mihomo/1.19.29"
        report = compare_captures(
            capture("official_mihomo"),
            candidate,
            official_asset=self.verified_asset,
        )
        self.assertFalse(report.equal)
        self.assertEqual(len(report.differences), 1)
        difference = report.differences[0]
        self.assertEqual(
            difference.path,
            "/layers/http1/requests/0/headers/1/value",
        )
        self.assertEqual(difference.kind, "value_mismatch")

    def test_structured_diff_reports_numeric_tolerance_exceeded(self):
        candidate = capture("subconverter", candidate=True)
        candidate["layers"]["application"]["elapsed_ms"] = 3000
        report = compare_captures(
            capture("official_mihomo"),
            candidate,
            official_asset=self.verified_asset,
        )
        self.assertFalse(report.equal)
        elapsed = [
            item
            for item in report.differences
            if item.path == "/layers/application/elapsed_ms"
        ]
        self.assertEqual(elapsed[0].kind, "numeric_tolerance_exceeded")

    def test_structured_diff_rejects_unverified_reference_subject(self):
        reference = capture("official_mihomo")
        reference["subject"]["artifact_sha256"] = "ef" * 32
        with self.assertRaises(OfficialAssetValidationError):
            compare_captures(
                reference,
                capture("subconverter", candidate=True),
                official_asset=self.verified_asset,
            )

    def test_structured_diff_binds_reference_architecture_to_asset(self):
        reference = capture("official_mihomo")
        reference["environment"]["architecture"] = "arm64"
        with self.assertRaisesRegex(
            OfficialAssetValidationError, "architecture differs"
        ):
            compare_captures(
                reference,
                capture("subconverter", candidate=True),
                official_asset=self.verified_asset,
            )

    def test_official_asset_metadata_and_bytes_are_bound(self):
        self.assertEqual(self.identity.variant, "compatible")
        self.assertEqual(self.identity.sha256, OFFICIAL_SHA256)
        self.assertEqual(self.verified_asset.observed_sha256, OFFICIAL_SHA256)
        validate_reference_capture_subject(
            capture("official_mihomo")["subject"], self.verified_asset
        )

    def test_official_asset_rejects_draft_or_prerelease(self):
        for field in ("draft", "prerelease"):
            with self.subTest(field=field):
                arguments = {field: True}
                with self.assertRaisesRegex(
                    OfficialAssetValidationError, "stable and published"
                ):
                    validate_official_asset_metadata(
                        release_metadata(**arguments),
                        expected_tag=TAG,
                        expected_asset_name=ASSET_NAME,
                        platform="linux",
                        architecture="amd64",
                    )

    def test_official_asset_rejects_unofficial_download_url(self):
        metadata = release_metadata()
        metadata["assets"][0]["browser_download_url"] = (
            "https://example.invalid/mihomo.gz"
        )
        with self.assertRaisesRegex(
            OfficialAssetValidationError, "download URL is not official"
        ):
            validate_official_asset_metadata(
                metadata,
                expected_tag=TAG,
                expected_asset_name=ASSET_NAME,
                platform="linux",
                architecture="amd64",
            )

    def test_official_asset_rejects_digest_mismatch(self):
        same_size_wrong_bytes = b"x" * len(OFFICIAL_BYTES)
        self.asset_path.write_bytes(same_size_wrong_bytes)
        with self.assertRaisesRegex(
            OfficialAssetValidationError, "sha256 does not match"
        ):
            verify_official_asset_file(self.identity, self.asset_path)


if __name__ == "__main__":
    unittest.main()
