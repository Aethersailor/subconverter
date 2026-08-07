"""Deterministic normalization for outbound conformance captures."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from .policy import load_normalization_policy
from .schema import validate_capture


def _decode_pointer_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def split_pointer(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer: {pointer!r}")
    return [_decode_pointer_token(token) for token in pointer[1:].split("/")]


def pointer_matches(pattern: str, pointer: str) -> bool:
    pattern_tokens = split_pointer(pattern)
    pointer_tokens = split_pointer(pointer)
    return len(pattern_tokens) == len(pointer_tokens) and all(
        expected == "*" or expected == actual
        for expected, actual in zip(pattern_tokens, pointer_tokens)
    )


def _mask_value(
    value: Any, tokens: list[str], replacement: Any, index: int = 0
) -> tuple[Any, int]:
    if index == len(tokens):
        return replacement, 1

    token = tokens[index]
    matches = 0
    if token == "*":
        if not isinstance(value, list):
            return value, 0
        for item_index, item in enumerate(value):
            value[item_index], item_matches = _mask_value(
                item, tokens, replacement, index + 1
            )
            matches += item_matches
        return value, matches

    if not isinstance(value, dict) or token not in value:
        return value, 0
    value[token], matches = _mask_value(
        value[token], tokens, replacement, index + 1
    )
    return value, matches


def normalize_capture(
    capture: Mapping[str, Any], policy: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Validate and normalize a capture without mutating the input.

    Masking replaces only fields on the audited allowlist. Fields stay present,
    so a collector cannot turn an uncaptured value into an allowed omission.
    """

    validate_capture(capture)
    loaded_policy = dict(policy) if policy is not None else load_normalization_policy()
    # Re-load validation for caller-provided dictionaries as well.
    if policy is not None:
        from .policy import validate_normalization_policy

        validate_normalization_policy(loaded_policy)

    normalized = copy.deepcopy(dict(capture))
    for rule in loaded_policy["masks"]:
        normalized, _ = _mask_value(
            normalized, split_pointer(rule["path"]), rule["replacement"]
        )
    return normalized


def numeric_tolerance_for_path(
    path: str, policy: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    for rule in policy["numeric_tolerances"]:
        if pointer_matches(rule["path"], path):
            return rule
    return None
