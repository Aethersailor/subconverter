"""Versioned capture schema loading and fail-closed validation."""

from __future__ import annotations

import ipaddress
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


CAPTURE_SCHEMA_VERSION = 1
CAPTURE_SCHEMA_FILE = Path(__file__).with_name("capture-v1.schema.json")
SUPPORTED_PROFILES = frozenset(
    {"http1_plaintext", "https_http1", "https_http2"}
)


class CaptureValidationError(ValueError):
    """Raised when a capture is incomplete or outside the v1 contract."""

    def __init__(self, path: str, message: str):
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


def load_capture_schema() -> dict[str, Any]:
    """Load the checked-in JSON schema without accessing the network."""

    with CAPTURE_SCHEMA_FILE.open(encoding="utf-8") as stream:
        schema = json.load(stream)
    if schema.get("$id") != (
        "urn:aethersailor:subconverter:mihomo-conformance:capture:1"
    ):
        raise RuntimeError("unexpected capture schema identity")
    return schema


def _pointer(path: str, token: str | int) -> str:
    text = str(token).replace("~", "~0").replace("/", "~1")
    return f"{path}/{text}" if path else f"/{text}"


def _resolve_local_ref(root: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    if not ref.startswith("#/"):
        raise RuntimeError(f"external schema references are forbidden: {ref}")
    value: Any = root
    for raw_token in ref[2:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, Mapping) or token not in value:
            raise RuntimeError(f"invalid local schema reference: {ref}")
        value = value[token]
    if not isinstance(value, Mapping):
        raise RuntimeError(f"schema reference does not resolve to an object: {ref}")
    return value


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    raise RuntimeError(f"unsupported schema type: {expected}")


def _validate_format(value: str, format_name: str, path: str) -> None:
    try:
        if format_name == "uuid":
            uuid.UUID(value)
        elif format_name == "date-time":
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone is required")
        elif format_name == "ip-address":
            ipaddress.ip_address(value)
        else:
            raise RuntimeError(f"unsupported schema format: {format_name}")
    except ValueError as error:
        raise CaptureValidationError(path, f"invalid {format_name}: {error}") from error


def _validate_instance(
    value: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: str,
) -> None:
    if "$ref" in schema:
        _validate_instance(value, _resolve_local_ref(root, schema["$ref"]), root, path)
        return

    if "oneOf" in schema:
        successes = 0
        branch_errors: list[str] = []
        for branch in schema["oneOf"]:
            try:
                _validate_instance(value, branch, root, path)
                successes += 1
            except CaptureValidationError as error:
                branch_errors.append(str(error))
        if successes != 1:
            details = "; ".join(branch_errors)
            raise CaptureValidationError(
                path, f"must match exactly one schema branch ({details})"
            )
        return

    if "const" in schema and value != schema["const"]:
        raise CaptureValidationError(path, f"must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise CaptureValidationError(path, f"unsupported value {value!r}")

    expected_type = schema.get("type")
    if expected_type is not None and not _type_matches(value, expected_type):
        raise CaptureValidationError(path, f"must be a JSON {expected_type}")

    if expected_type == "object":
        required = set(schema.get("required", []))
        missing = sorted(required.difference(value))
        if missing:
            raise CaptureValidationError(path, f"missing required fields: {missing}")

        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value).difference(properties))
            if unknown:
                raise CaptureValidationError(path, f"unknown fields: {unknown}")
        for key, item in value.items():
            if key in properties:
                _validate_instance(item, properties[key], root, _pointer(path, key))

    elif expected_type == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise CaptureValidationError(path, "contains too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise CaptureValidationError(path, "contains too many items")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                _validate_instance(item, item_schema, root, _pointer(path, index))

    elif expected_type == "string":
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise CaptureValidationError(path, "is shorter than the minimum length")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise CaptureValidationError(path, "does not match the required pattern")
        if "format" in schema:
            _validate_format(value, schema["format"], path)

    elif expected_type in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise CaptureValidationError(path, f"must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise CaptureValidationError(path, f"must be <= {schema['maximum']}")


def validate_capture(capture: Mapping[str, Any]) -> None:
    """Validate syntax, completeness, and profile-required capture layers.

    The checked-in schema rejects unknown fields. The checked-in policy then
    verifies that a profile did not mark a required TLS or HTTP layer as
    ``not_applicable``. There is deliberately no ``not_captured`` state.
    """

    schema = load_capture_schema()
    _validate_instance(capture, schema, schema, "")

    from .policy import enforce_capture_profile, load_normalization_policy

    policy = load_normalization_policy()
    enforce_capture_profile(capture, policy)


def validate_capture_collection(captures: Sequence[Mapping[str, Any]]) -> None:
    """Validate a non-empty capture collection and reject duplicate IDs."""

    if not captures:
        raise CaptureValidationError("", "capture collection must not be empty")
    seen: set[str] = set()
    for index, capture in enumerate(captures):
        try:
            validate_capture(capture)
        except CaptureValidationError as error:
            raise CaptureValidationError(
                _pointer("", index) + error.path, error.message
            ) from error
        capture_id = capture["capture_id"]
        if capture_id in seen:
            raise CaptureValidationError(
                _pointer(_pointer("", index), "capture_id"),
                "duplicate capture_id",
            )
        seen.add(capture_id)
