"""Offline primitives for Mihomo outbound conformance verification."""

from .diff import DiffReport, Difference, compare_captures
from .identity import (
    OfficialAssetIdentity,
    OfficialAssetValidationError,
    VerifiedOfficialAsset,
    validate_official_asset_metadata,
    validate_reference_capture_identity,
    validate_reference_capture_subject,
    verify_official_asset_file,
)
from .normalization import normalize_capture
from .policy import (
    PolicyValidationError,
    load_normalization_policy,
    validate_normalization_policy,
)
from .schema import (
    CAPTURE_SCHEMA_VERSION,
    CaptureValidationError,
    load_capture_schema,
    validate_capture,
    validate_capture_collection,
)

__all__ = [
    "CAPTURE_SCHEMA_VERSION",
    "CaptureValidationError",
    "DiffReport",
    "Difference",
    "OfficialAssetIdentity",
    "OfficialAssetValidationError",
    "PolicyValidationError",
    "VerifiedOfficialAsset",
    "compare_captures",
    "load_capture_schema",
    "load_normalization_policy",
    "normalize_capture",
    "validate_capture",
    "validate_capture_collection",
    "validate_official_asset_metadata",
    "validate_reference_capture_identity",
    "validate_normalization_policy",
    "validate_reference_capture_subject",
    "verify_official_asset_file",
]
