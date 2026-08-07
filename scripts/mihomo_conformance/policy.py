"""Static normalization policy loading and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .schema import (
    CAPTURE_SCHEMA_VERSION,
    SUPPORTED_PROFILES,
    CaptureValidationError,
)


NORMALIZATION_POLICY_VERSION = 1
NORMALIZATION_POLICY_FILE = Path(__file__).with_name(
    "normalization-policy-v1.json"
)
LAYER_NAMES = frozenset(
    {"dns", "tcp", "tls", "http1", "http2", "application"}
)
ALLOWED_MASK_PATHS = frozenset(
    {
        "/capture_id",
        "/captured_at",
        "/subject/kind",
        "/subject/name",
        "/subject/version",
        "/subject/artifact_sha256",
        "/layers/dns/queries/*/timestamp_ns",
        "/layers/dns/queries/*/transaction_id",
        "/layers/tcp/connections/*/timestamp_ns",
        "/layers/tcp/connections/*/source_port",
        "/layers/tls/handshakes/*/timestamp_ns",
        "/layers/tls/handshakes/*/client_random",
        "/layers/tls/handshakes/*/session_id",
        "/layers/tls/handshakes/*/key_share_public_values/*",
        "/layers/http1/requests/*/timestamp_ns",
        "/layers/http2/requests/*/timestamp_ns",
    }
)
ALLOWED_TOLERANCE_PATHS = frozenset({"/layers/application/elapsed_ms"})


class PolicyValidationError(RuntimeError):
    """Raised when the checked-in policy is malformed or over-broad."""


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], context: str
) -> None:
    actual = set(value)
    if actual != expected:
        raise PolicyValidationError(
            f"{context} keys differ: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def validate_normalization_policy(policy: Mapping[str, Any]) -> None:
    _require_exact_keys(
        policy,
        {
            "policy_version",
            "capture_schema_version",
            "profiles",
            "masks",
            "numeric_tolerances",
        },
        "policy",
    )
    if policy["policy_version"] != NORMALIZATION_POLICY_VERSION:
        raise PolicyValidationError("unsupported normalization policy version")
    if policy["capture_schema_version"] != CAPTURE_SCHEMA_VERSION:
        raise PolicyValidationError("policy and capture schema versions differ")

    profiles = policy["profiles"]
    if not isinstance(profiles, Mapping):
        raise PolicyValidationError("profiles must be an object")
    if set(profiles) != set(SUPPORTED_PROFILES):
        raise PolicyValidationError("policy profiles and schema profiles differ")
    for profile_name, profile in profiles.items():
        if not isinstance(profile, Mapping):
            raise PolicyValidationError(f"profile {profile_name} must be an object")
        _require_exact_keys(profile, {"layers"}, f"profile {profile_name}")
        layers = profile["layers"]
        if not isinstance(layers, Mapping) or set(layers) != set(LAYER_NAMES):
            raise PolicyValidationError(
                f"profile {profile_name} must declare every capture layer"
            )
        invalid_statuses = {
            status for status in layers.values() if status not in {"captured", "not_applicable"}
        }
        if invalid_statuses:
            raise PolicyValidationError(
                f"profile {profile_name} has invalid statuses: {sorted(invalid_statuses)}"
            )

    masks = policy["masks"]
    if not isinstance(masks, list):
        raise PolicyValidationError("masks must be an array")
    mask_paths: list[str] = []
    for index, rule in enumerate(masks):
        if not isinstance(rule, Mapping):
            raise PolicyValidationError(f"mask {index} must be an object")
        _require_exact_keys(rule, {"path", "replacement"}, f"mask {index}")
        if not isinstance(rule["path"], str) or not rule["path"].startswith("/"):
            raise PolicyValidationError(f"mask {index} has an invalid path")
        if rule["replacement"] != "<masked>":
            raise PolicyValidationError(
                f"mask {index} must use the canonical replacement"
            )
        mask_paths.append(rule["path"])
    if len(mask_paths) != len(set(mask_paths)):
        raise PolicyValidationError("mask paths must be unique")
    if set(mask_paths) != set(ALLOWED_MASK_PATHS):
        raise PolicyValidationError("mask paths differ from the audited allowlist")

    tolerances = policy["numeric_tolerances"]
    if not isinstance(tolerances, list):
        raise PolicyValidationError("numeric_tolerances must be an array")
    tolerance_paths: list[str] = []
    for index, rule in enumerate(tolerances):
        if not isinstance(rule, Mapping):
            raise PolicyValidationError(f"tolerance {index} must be an object")
        _require_exact_keys(
            rule, {"path", "absolute", "relative"}, f"tolerance {index}"
        )
        path = rule["path"]
        if not isinstance(path, str) or not path.startswith("/"):
            raise PolicyValidationError(f"tolerance {index} has an invalid path")
        for key in ("absolute", "relative"):
            value = rule[key]
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
            ):
                raise PolicyValidationError(
                    f"tolerance {index} {key} must be a non-negative number"
                )
        tolerance_paths.append(path)
    if len(tolerance_paths) != len(set(tolerance_paths)):
        raise PolicyValidationError("tolerance paths must be unique")
    if set(tolerance_paths) != set(ALLOWED_TOLERANCE_PATHS):
        raise PolicyValidationError(
            "numeric tolerance paths differ from the audited allowlist"
        )
    if set(mask_paths).intersection(tolerance_paths):
        raise PolicyValidationError("a field cannot be both masked and tolerated")


def load_normalization_policy() -> dict[str, Any]:
    """Load and audit the static v1 policy."""

    with NORMALIZATION_POLICY_FILE.open(encoding="utf-8") as stream:
        policy = json.load(stream)
    validate_normalization_policy(policy)
    return policy


def enforce_capture_profile(
    capture: Mapping[str, Any], policy: Mapping[str, Any]
) -> None:
    """Reject captures that omit a layer required by their profile."""

    profile_name = capture["profile"]
    try:
        expected_layers = policy["profiles"][profile_name]["layers"]
    except (KeyError, TypeError) as error:
        raise CaptureValidationError(
            "/profile", f"profile {profile_name!r} has no static policy"
        ) from error

    for layer_name in sorted(LAYER_NAMES):
        expected_status = expected_layers[layer_name]
        actual_status = capture["layers"][layer_name]["status"]
        if actual_status != expected_status:
            raise CaptureValidationError(
                f"/layers/{layer_name}/status",
                f"profile {profile_name!r} requires {expected_status!r}, "
                f"got {actual_status!r}",
            )
