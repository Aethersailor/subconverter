"""Offline validation for pinned official Mihomo release assets."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


OFFICIAL_REPOSITORY = "MetaCubeX/mihomo"
ASSET_IDENTITY_SCHEMA_VERSION = 1
STABLE_TAG_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
SHA256_DIGEST_PATTERN = re.compile(r"^sha256:([0-9a-f]{64})$")
AMD64_VARIANT_PATTERN = re.compile(r"^(?:compatible|v[123](?:-go[0-9]+)?)$")


class OfficialAssetValidationError(ValueError):
    """Raised when release metadata or downloaded bytes are not authoritative."""


@dataclass(frozen=True)
class OfficialAssetIdentity:
    identity_schema_version: int
    repository: str
    release_id: int
    asset_id: int
    tag_name: str
    asset_name: str
    platform: str
    architecture: str
    variant: str
    size: int
    sha256: str
    download_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VerifiedOfficialAsset:
    identity: OfficialAssetIdentity
    observed_size: int
    observed_sha256: str

    def __post_init__(self) -> None:
        if self.observed_size != self.identity.size:
            raise OfficialAssetValidationError(
                "verified asset size is inconsistent with its identity"
            )
        if self.observed_sha256 != self.identity.sha256:
            raise OfficialAssetValidationError(
                "verified asset digest is inconsistent with its identity"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "verification": {
                "observed_size": self.observed_size,
                "observed_sha256": self.observed_sha256,
            },
        }


def _positive_integer(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise OfficialAssetValidationError(f"{field} must be a positive integer")
    return value


def _validate_asset_name(
    asset_name: str, tag_name: str, platform: str, architecture: str
) -> str:
    if not all(
        isinstance(value, str)
        for value in (asset_name, tag_name, platform, architecture)
    ):
        raise OfficialAssetValidationError("asset identity fields must be strings")
    if platform != "linux":
        raise OfficialAssetValidationError("only the Linux release oracle is supported")
    if architecture not in {"amd64", "arm64"}:
        raise OfficialAssetValidationError("unsupported official asset architecture")

    suffix = f"-{tag_name}.gz"
    prefix = f"mihomo-{platform}-{architecture}"
    if not asset_name.startswith(prefix) or not asset_name.endswith(suffix):
        raise OfficialAssetValidationError(
            "asset name does not bind platform, architecture, and release tag"
        )
    variant_with_dash = asset_name[len(prefix) : -len(suffix)]
    if architecture == "arm64":
        if variant_with_dash:
            raise OfficialAssetValidationError(
                "the canonical Linux arm64 asset must not have a variant suffix"
            )
        return ""

    if not variant_with_dash.startswith("-"):
        raise OfficialAssetValidationError(
            "the Linux amd64 asset must select an explicit CPU variant"
        )
    variant = variant_with_dash[1:]
    if AMD64_VARIANT_PATTERN.fullmatch(variant) is None:
        raise OfficialAssetValidationError("unsupported Linux amd64 asset variant")
    return variant


def validate_official_asset_metadata(
    release: Mapping[str, Any],
    *,
    expected_tag: str,
    expected_asset_name: str,
    platform: str,
    architecture: str,
) -> OfficialAssetIdentity:
    """Validate already-fetched GitHub release JSON and select one exact asset.

    This function performs no network access and deliberately does not resolve
    ``latest``. The caller must pass the tag and asset name fixed earlier in the
    release pipeline, preventing a second moving-target lookup.
    """

    if STABLE_TAG_PATTERN.fullmatch(expected_tag) is None:
        raise OfficialAssetValidationError("expected_tag is not a stable tag")
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise OfficialAssetValidationError("release must be stable and published")
    if release.get("tag_name") != expected_tag:
        raise OfficialAssetValidationError("release tag differs from the pinned tag")

    release_id = _positive_integer(release.get("id"), "release id")
    expected_release_api_url = (
        f"https://api.github.com/repos/{OFFICIAL_REPOSITORY}/releases/{release_id}"
    )
    if release.get("url") != expected_release_api_url:
        raise OfficialAssetValidationError("release API URL is not official")
    expected_release_page = (
        f"https://github.com/{OFFICIAL_REPOSITORY}/releases/tag/{expected_tag}"
    )
    if release.get("html_url") != expected_release_page:
        raise OfficialAssetValidationError("release page URL is not official")

    assets = release.get("assets")
    if not isinstance(assets, list):
        raise OfficialAssetValidationError("release assets must be an array")
    matches = [
        asset
        for asset in assets
        if isinstance(asset, Mapping) and asset.get("name") == expected_asset_name
    ]
    if len(matches) != 1:
        raise OfficialAssetValidationError(
            "the pinned asset name must occur exactly once in release metadata"
        )
    asset = matches[0]

    variant = _validate_asset_name(
        expected_asset_name, expected_tag, platform, architecture
    )
    asset_id = _positive_integer(asset.get("id"), "asset id")
    size = _positive_integer(asset.get("size"), "asset size")
    if asset.get("state") != "uploaded":
        raise OfficialAssetValidationError("release asset is not fully uploaded")

    digest_match = SHA256_DIGEST_PATTERN.fullmatch(str(asset.get("digest", "")))
    if digest_match is None:
        raise OfficialAssetValidationError(
            "release asset must provide a lowercase sha256 digest"
        )
    sha256 = digest_match.group(1)

    expected_asset_api_url = (
        f"https://api.github.com/repos/{OFFICIAL_REPOSITORY}/releases/assets/{asset_id}"
    )
    if asset.get("url") != expected_asset_api_url:
        raise OfficialAssetValidationError("asset API URL is not official")
    expected_download_url = (
        f"https://github.com/{OFFICIAL_REPOSITORY}/releases/download/"
        f"{expected_tag}/{expected_asset_name}"
    )
    if asset.get("browser_download_url") != expected_download_url:
        raise OfficialAssetValidationError("asset download URL is not official")

    return OfficialAssetIdentity(
        identity_schema_version=ASSET_IDENTITY_SCHEMA_VERSION,
        repository=OFFICIAL_REPOSITORY,
        release_id=release_id,
        asset_id=asset_id,
        tag_name=expected_tag,
        asset_name=expected_asset_name,
        platform=platform,
        architecture=architecture,
        variant=variant,
        size=size,
        sha256=sha256,
        download_url=expected_download_url,
    )


def verify_official_asset_file(
    identity: OfficialAssetIdentity, path: str | Path
) -> VerifiedOfficialAsset:
    """Hash a local official asset and require exact size and digest identity."""

    asset_path = Path(path)
    if not asset_path.is_file():
        raise OfficialAssetValidationError("official asset path is not a regular file")

    digest = hashlib.sha256()
    observed_size = 0
    with asset_path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            observed_size += len(chunk)
            digest.update(chunk)
    observed_sha256 = digest.hexdigest()

    if observed_size != identity.size:
        raise OfficialAssetValidationError(
            f"official asset size mismatch: expected {identity.size}, "
            f"observed {observed_size}"
        )
    if observed_sha256 != identity.sha256:
        raise OfficialAssetValidationError(
            "official asset sha256 does not match release metadata"
        )
    return VerifiedOfficialAsset(
        identity=identity,
        observed_size=observed_size,
        observed_sha256=observed_sha256,
    )


def validate_reference_capture_subject(
    subject: Mapping[str, Any], verified_asset: VerifiedOfficialAsset
) -> None:
    """Bind an oracle capture to bytes that passed local digest verification."""

    expected = {
        "kind": "official_mihomo",
        "name": "mihomo",
        "version": verified_asset.identity.tag_name,
        "artifact_sha256": verified_asset.observed_sha256,
    }
    if dict(subject) != expected:
        raise OfficialAssetValidationError(
            "reference capture subject does not match the verified official asset"
        )


def validate_reference_capture_identity(
    capture: Mapping[str, Any], verified_asset: VerifiedOfficialAsset
) -> None:
    """Bind oracle subject and execution platform to the verified release asset."""

    validate_reference_capture_subject(capture["subject"], verified_asset)
    environment = capture["environment"]
    if environment["os"] != verified_asset.identity.platform:
        raise OfficialAssetValidationError(
            "reference capture OS differs from the official asset platform"
        )
    if environment["architecture"] != verified_asset.identity.architecture:
        raise OfficialAssetValidationError(
            "reference capture architecture differs from the official asset"
        )
