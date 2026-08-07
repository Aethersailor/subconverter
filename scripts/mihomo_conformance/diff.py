"""Structured, fail-closed comparison of normalized conformance captures."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .identity import (
    VerifiedOfficialAsset,
    validate_reference_capture_identity,
)
from .normalization import normalize_capture, numeric_tolerance_for_path
from .policy import load_normalization_policy
from .schema import CAPTURE_SCHEMA_VERSION, CaptureValidationError, validate_capture


DIFF_REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Difference:
    path: str
    kind: str
    reference: Any
    candidate: Any
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiffReport:
    report_schema_version: int
    capture_schema_version: int
    policy_version: int
    scenario_id: str
    profile: str
    reference_capture_id: str
    candidate_capture_id: str
    official_asset: Mapping[str, Any]
    equal: bool
    differences: tuple[Difference, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["differences"] = [item.to_dict() for item in self.differences]
        return result

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True
        )


def _pointer(path: str, token: str | int) -> str:
    text = str(token).replace("~", "~0").replace("/", "~1")
    return f"{path}/{text}" if path else f"/{text}"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _compare_values(
    reference: Any,
    candidate: Any,
    path: str,
    policy: Mapping[str, Any],
    differences: list[Difference],
) -> None:
    if type(reference) is not type(candidate):
        differences.append(
            Difference(
                path=path or "/",
                kind="type_mismatch",
                reference=reference,
                candidate=candidate,
                detail=(
                    f"reference type {type(reference).__name__}, "
                    f"candidate type {type(candidate).__name__}"
                ),
            )
        )
        return

    if isinstance(reference, dict):
        reference_keys = set(reference)
        candidate_keys = set(candidate)
        for key in sorted(reference_keys - candidate_keys):
            differences.append(
                Difference(
                    path=_pointer(path, key),
                    kind="missing_in_candidate",
                    reference=reference[key],
                    candidate=None,
                    detail="candidate omitted a captured field",
                )
            )
        for key in sorted(candidate_keys - reference_keys):
            differences.append(
                Difference(
                    path=_pointer(path, key),
                    kind="unexpected_in_candidate",
                    reference=None,
                    candidate=candidate[key],
                    detail="candidate added an unrecognized captured field",
                )
            )
        for key in sorted(reference_keys.intersection(candidate_keys)):
            _compare_values(
                reference[key],
                candidate[key],
                _pointer(path, key),
                policy,
                differences,
            )
        return

    if isinstance(reference, list):
        if len(reference) != len(candidate):
            differences.append(
                Difference(
                    path=path or "/",
                    kind="length_mismatch",
                    reference=len(reference),
                    candidate=len(candidate),
                    detail="ordered capture arrays have different lengths",
                )
            )
        for index in range(min(len(reference), len(candidate))):
            _compare_values(
                reference[index],
                candidate[index],
                _pointer(path, index),
                policy,
                differences,
            )
        return

    tolerance = numeric_tolerance_for_path(path or "/", policy)
    if tolerance is not None and _is_number(reference) and _is_number(candidate):
        allowed_delta = max(
            float(tolerance["absolute"]),
            abs(float(reference)) * float(tolerance["relative"]),
        )
        actual_delta = abs(float(reference) - float(candidate))
        if actual_delta > allowed_delta:
            differences.append(
                Difference(
                    path=path or "/",
                    kind="numeric_tolerance_exceeded",
                    reference=reference,
                    candidate=candidate,
                    detail=(
                        f"delta {actual_delta:g} exceeds allowed "
                        f"{allowed_delta:g}"
                    ),
                )
            )
        return

    if reference != candidate:
        differences.append(
            Difference(
                path=path or "/",
                kind="value_mismatch",
                reference=reference,
                candidate=candidate,
                detail="normalized values differ",
            )
        )


def compare_captures(
    reference: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    official_asset: VerifiedOfficialAsset,
) -> DiffReport:
    """Compare one official Mihomo capture with one SubConverter capture.

    Validation errors are raised instead of converted to a passing or partial
    report. This makes missing TLS/H2 observations, collector errors, unknown
    fields, and an unverified oracle hard failures.
    """

    validate_capture(reference)
    validate_capture(candidate)
    validate_reference_capture_identity(reference, official_asset)
    if candidate["subject"]["kind"] != "subconverter":
        raise CaptureValidationError(
            "/subject/kind", "candidate capture must be SubConverter"
        )
    if reference["scenario_id"] != candidate["scenario_id"]:
        raise CaptureValidationError(
            "/scenario_id", "reference and candidate scenarios differ"
        )
    if reference["profile"] != candidate["profile"]:
        raise CaptureValidationError(
            "/profile", "reference and candidate capture profiles differ"
        )

    policy = load_normalization_policy()
    normalized_reference = normalize_capture(reference, policy)
    normalized_candidate = normalize_capture(candidate, policy)
    differences: list[Difference] = []
    _compare_values(
        normalized_reference, normalized_candidate, "", policy, differences
    )

    return DiffReport(
        report_schema_version=DIFF_REPORT_SCHEMA_VERSION,
        capture_schema_version=CAPTURE_SCHEMA_VERSION,
        policy_version=policy["policy_version"],
        scenario_id=reference["scenario_id"],
        profile=reference["profile"],
        reference_capture_id=reference["capture_id"],
        candidate_capture_id=candidate["capture_id"],
        official_asset=official_asset.to_dict(),
        equal=not differences,
        differences=tuple(differences),
    )
