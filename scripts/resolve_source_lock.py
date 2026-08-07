#!/usr/bin/env python3

"""Resolve immutable SubConverter and Mihomo source identities.

The resolver intentionally uses only GitHub's public REST API and never reads
credentials. Tests and CI dry-runs can provide a directory containing a
``responses.json`` mapping to resolve the same lock fully offline.
"""

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK_PATH = ROOT / ".github" / "source-lock.json"
DEFAULT_API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"

SUBCONVERTER_REPOSITORY = "asdlokj1qpi233/subconverter"
SUBCONVERTER_BRANCH = "master"
SUBCONVERTER_VERSION_PATH = "src/version.h"
MIHOMO_REPOSITORY = "MetaCubeX/mihomo"
HELPER_PROTOCOL_VERSION = 1
PARITY_CONTRACT = "mihomo-provider-fetch-v1"
HELPER_OVERLAY_FILES = (
    "mihomo-fetcher/cmd/subconverter-mihomo-fetcher/main.go",
)

SEMVER_RE = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

REQUIRED_RELEASE_ASSETS = {
    "toolchain": "toolchain.tar.gz",
    "vendor": "vendor.tar.gz",
}

# Explicit v1 amd64 variants preserve the generic amd64 compatibility of the
# repository's current native and container publication matrix.
ORACLE_ASSET_TEMPLATES = {
    "linux-386": "mihomo-linux-386-{tag}.gz",
    "linux-amd64": "mihomo-linux-amd64-v1-{tag}.gz",
    "linux-armv7": "mihomo-linux-armv7-{tag}.gz",
    "linux-arm64": "mihomo-linux-arm64-{tag}.gz",
    "macos-amd64": "mihomo-darwin-amd64-v1-{tag}.gz",
    "macos-arm64": "mihomo-darwin-arm64-{tag}.gz",
    "windows-386": "mihomo-windows-386-{tag}.zip",
    "windows-amd64": "mihomo-windows-amd64-v1-{tag}.zip",
}


class SourceLockError(RuntimeError):
    """Raised when a remote identity is incomplete, mutable, or downgraded."""


class GitHubClient:
    """Small unauthenticated GitHub REST client."""

    def __init__(self, api_base=DEFAULT_API_BASE, timeout=30):
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    def get_json(self, path):
        url = self.api_base + path
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "subconverter-source-lock-resolver/1",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            raise SourceLockError("GitHub request failed for {}: {}".format(path, error))
        try:
            return json.loads(payload)
        except json.JSONDecodeError as error:
            raise SourceLockError("GitHub returned invalid JSON for {}: {}".format(path, error))


class FixtureGitHubClient:
    """Offline client backed by ``<fixture_dir>/responses.json``."""

    def __init__(self, fixture_dir):
        fixture_path = Path(fixture_dir) / "responses.json"
        try:
            self.responses = json.loads(fixture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SourceLockError("unable to load fixture {}: {}".format(fixture_path, error))
        if not isinstance(self.responses, dict):
            raise SourceLockError("fixture responses.json must contain an object")

    def get_json(self, path):
        if path not in self.responses:
            raise SourceLockError("fixture has no response for {}".format(path))
        return copy.deepcopy(self.responses[path])


def _api_path(repository, suffix):
    return "/repos/{}/{}".format(repository, suffix.lstrip("/"))


def _require_mapping(value, label):
    if not isinstance(value, dict):
        raise SourceLockError("{} must be an object".format(label))
    return value


def _require_string(value, label):
    if not isinstance(value, str) or not value:
        raise SourceLockError("{} must be a non-empty string".format(label))
    return value


def _require_sha(value, label):
    value = _require_string(value, label)
    if not SHA_RE.fullmatch(value):
        raise SourceLockError("{} must be a full lowercase Git SHA".format(label))
    return value


def _parse_semver(tag, label="release tag"):
    tag = _require_string(tag, label)
    match = SEMVER_RE.fullmatch(tag)
    if not match:
        raise SourceLockError("{} must use vMAJOR.MINOR.PATCH".format(label))
    return tuple(int(part) for part in match.groups())


def _verification_summary(value):
    if not isinstance(value, dict):
        return {"reason": "missing", "verified": False, "verified_at": None}
    verified_at = value.get("verified_at")
    if verified_at is not None and not isinstance(verified_at, str):
        raise SourceLockError("commit verification timestamp must be a string or null")
    return {
        "reason": str(value.get("reason") or "unknown"),
        "verified": value.get("verified") is True,
        "verified_at": verified_at,
    }


def _resolve_commit(client, repository, commit_sha):
    commit_sha = _require_sha(commit_sha, "commit SHA")
    response = _require_mapping(
        client.get_json(_api_path(repository, "git/commits/{}".format(commit_sha))),
        "Git commit response",
    )
    resolved_sha = _require_sha(response.get("sha"), "resolved commit SHA")
    if resolved_sha != commit_sha:
        raise SourceLockError(
            "resolved commit {} does not match requested {}".format(resolved_sha, commit_sha)
        )
    tree = _require_mapping(response.get("tree"), "Git commit tree")
    return {
        "commit": resolved_sha,
        "tree": _require_sha(tree.get("sha"), "Git tree SHA"),
        "verification": _verification_summary(response.get("verification")),
    }


def _resolve_tag(client, repository, tag_name):
    encoded_tag = urllib.parse.quote(tag_name, safe="")
    ref = _require_mapping(
        client.get_json(_api_path(repository, "git/ref/tags/{}".format(encoded_tag))),
        "tag reference",
    )
    ref_object = _require_mapping(ref.get("object"), "tag reference object")
    ref_type = _require_string(ref_object.get("type"), "tag reference object type")
    ref_sha = _require_sha(ref_object.get("sha"), "tag reference object SHA")

    current_type = ref_type
    current_sha = ref_sha
    annotated_tags = []
    visited = set()
    for _ in range(8):
        if current_type == "commit":
            break
        if current_type != "tag":
            raise SourceLockError("tag {} resolves to unsupported object type {}".format(tag_name, current_type))
        if current_sha in visited:
            raise SourceLockError("tag {} contains a cycle".format(tag_name))
        visited.add(current_sha)

        tag_object = _require_mapping(
            client.get_json(_api_path(repository, "git/tags/{}".format(current_sha))),
            "annotated tag object",
        )
        resolved_tag_sha = _require_sha(tag_object.get("sha"), "annotated tag SHA")
        if resolved_tag_sha != current_sha:
            raise SourceLockError("annotated tag response does not match requested object")
        target = _require_mapping(tag_object.get("object"), "annotated tag target")
        target_type = _require_string(target.get("type"), "annotated tag target type")
        target_sha = _require_sha(target.get("sha"), "annotated tag target SHA")
        annotated_tags.append(
            {
                "name": _require_string(tag_object.get("tag"), "annotated tag name"),
                "sha": resolved_tag_sha,
                "target_sha": target_sha,
                "target_type": target_type,
                "verification": _verification_summary(tag_object.get("verification")),
            }
        )
        current_type = target_type
        current_sha = target_sha
    else:
        raise SourceLockError("tag {} exceeds the maximum annotation depth".format(tag_name))

    commit = _resolve_commit(client, repository, current_sha)
    return {
        "annotated_tags": annotated_tags,
        "commit": commit["commit"],
        "commit_verification": commit["verification"],
        "ref_object_sha": ref_sha,
        "ref_object_type": ref_type,
        "tree": commit["tree"],
    }


def _resolve_subconverter(client):
    encoded_branch = urllib.parse.quote(SUBCONVERTER_BRANCH, safe="")
    ref = _require_mapping(
        client.get_json(
            _api_path(SUBCONVERTER_REPOSITORY, "git/ref/heads/{}".format(encoded_branch))
        ),
        "upstream branch reference",
    )
    ref_object = _require_mapping(ref.get("object"), "upstream branch object")
    if ref_object.get("type") != "commit":
        raise SourceLockError("upstream branch must resolve directly to a commit")
    commit = _resolve_commit(
        client,
        SUBCONVERTER_REPOSITORY,
        _require_sha(ref_object.get("sha"), "upstream branch commit"),
    )

    encoded_path = urllib.parse.quote(SUBCONVERTER_VERSION_PATH, safe="/")
    version_response = _require_mapping(
        client.get_json(
            _api_path(
                SUBCONVERTER_REPOSITORY,
                "contents/{}?ref={}".format(encoded_path, commit["commit"]),
            )
        ),
        "upstream version file",
    )
    if version_response.get("type") != "file" or version_response.get("encoding") != "base64":
        raise SourceLockError("upstream version response must be a base64 file")
    try:
        version_source = base64.b64decode(
            _require_string(version_response.get("content"), "upstream version content"),
            validate=False,
        ).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise SourceLockError("unable to decode upstream version file: {}".format(error))
    version_match = re.search(r'^#define VERSION "([^"]+)"$', version_source, re.MULTILINE)
    if not version_match:
        raise SourceLockError("unable to find VERSION in upstream src/version.h")
    version = version_match.group(1)
    _parse_semver(version, "upstream version")

    return {
        "branch": SUBCONVERTER_BRANCH,
        "commit": commit["commit"],
        "commit_verification": commit["verification"],
        "repository": SUBCONVERTER_REPOSITORY,
        "tree": commit["tree"],
        "version": version,
        "version_file": SUBCONVERTER_VERSION_PATH,
        "version_file_blob": _require_sha(
            version_response.get("sha"), "upstream version file blob"
        ),
    }


def _asset_identity(asset, expected_name):
    asset = _require_mapping(asset, "release asset {}".format(expected_name))
    name = _require_string(asset.get("name"), "release asset name")
    if name != expected_name:
        raise SourceLockError("expected release asset {}, got {}".format(expected_name, name))
    if asset.get("state") != "uploaded":
        raise SourceLockError("release asset {} is not uploaded".format(name))
    asset_id = asset.get("id")
    size = asset.get("size")
    if not isinstance(asset_id, int) or asset_id <= 0:
        raise SourceLockError("release asset {} has an invalid ID".format(name))
    if not isinstance(size, int) or size <= 0:
        raise SourceLockError("release asset {} has an invalid size".format(name))
    digest = _require_string(asset.get("digest"), "release asset digest")
    if not DIGEST_RE.fullmatch(digest):
        raise SourceLockError("release asset {} lacks a valid GitHub SHA-256 digest".format(name))
    return {
        "digest": digest,
        "download_url": _require_string(
            asset.get("browser_download_url"), "release asset download URL"
        ),
        "id": asset_id,
        "name": name,
        "size": size,
    }


def _select_assets(release, tag_name):
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise SourceLockError("release assets must be an array")
    by_name = {}
    for asset in assets:
        asset = _require_mapping(asset, "release asset")
        name = _require_string(asset.get("name"), "release asset name")
        if name in by_name:
            raise SourceLockError("release contains duplicate asset {}".format(name))
        by_name[name] = asset

    required = {}
    for key, name in REQUIRED_RELEASE_ASSETS.items():
        if name not in by_name:
            raise SourceLockError("release is missing required asset {}".format(name))
        required[key] = _asset_identity(by_name[name], name)

    oracles = {}
    for platform, template in ORACLE_ASSET_TEMPLATES.items():
        name = template.format(tag=tag_name)
        if name not in by_name:
            raise SourceLockError("release is missing oracle asset {}".format(name))
        oracles[platform] = _asset_identity(by_name[name], name)
    return required, oracles


def _resolve_mihomo(client):
    release = _require_mapping(
        client.get_json(_api_path(MIHOMO_REPOSITORY, "releases/latest")),
        "latest Mihomo release",
    )
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise SourceLockError("latest Mihomo release must be stable, published, and non-draft")
    release_id = release.get("id")
    if not isinstance(release_id, int) or release_id <= 0:
        raise SourceLockError("latest Mihomo release has an invalid ID")
    tag_name = _require_string(release.get("tag_name"), "Mihomo release tag")
    _parse_semver(tag_name, "Mihomo release tag")
    tag = _resolve_tag(client, MIHOMO_REPOSITORY, tag_name)
    required_assets, oracle_assets = _select_assets(release, tag_name)

    immutable = release.get("immutable")
    if immutable not in (True, False, None):
        raise SourceLockError("Mihomo release immutable field must be boolean or null")
    return {
        "created_at": _require_string(release.get("created_at"), "release creation time"),
        "immutable": immutable,
        "oracle_assets": oracle_assets,
        "oracle_profile": "published-platforms-v1",
        "published_at": _require_string(
            release.get("published_at"), "release publication time"
        ),
        "release_id": release_id,
        "release_url": _require_string(release.get("html_url"), "release URL"),
        "repository": MIHOMO_REPOSITORY,
        "required_assets": required_assets,
        "tag": tag_name,
        "tag_identity": tag,
    }


def _canonical_bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _file_set_sha256(paths):
    identities = {}
    for relative in paths:
        path = ROOT / relative
        try:
            content = path.read_bytes()
        except OSError as error:
            raise SourceLockError("unable to read project overlay {}: {}".format(path, error))
        identities[relative] = hashlib.sha256(content).hexdigest()
    return "sha256:" + hashlib.sha256(_canonical_bytes(identities)).hexdigest()


def _resolve_project_identity():
    return {
        "helper_overlay_sha256": _file_set_sha256(HELPER_OVERLAY_FILES),
        "helper_protocol": HELPER_PROTOCOL_VERSION,
        "parity_contract": PARITY_CONTRACT,
    }


def _pair_id(subconverter, mihomo, project):
    payload = {
        "mihomo": mihomo,
        "project": project,
        "subconverter": subconverter,
    }
    return "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _validate_lock_integrity(lock):
    lock = _require_mapping(lock, "existing source lock")
    if lock.get("schema_version") != 1:
        raise SourceLockError("existing source lock has an unsupported schema version")
    subconverter = _require_mapping(lock.get("subconverter"), "locked SubConverter identity")
    mihomo = _require_mapping(lock.get("mihomo"), "locked Mihomo identity")
    project = _require_mapping(lock.get("project"), "locked project identity")
    expected_pair_id = _pair_id(subconverter, mihomo, project)
    if lock.get("pair_id") != expected_pair_id:
        raise SourceLockError("existing source lock pair_id does not match its contents")


def _validate_transition(previous, current):
    if previous is None:
        return
    _validate_lock_integrity(previous)
    previous_mihomo = _require_mapping(previous.get("mihomo"), "locked Mihomo identity")
    current_mihomo = _require_mapping(current.get("mihomo"), "resolved Mihomo identity")
    previous_tag = _require_string(previous_mihomo.get("tag"), "locked Mihomo tag")
    current_tag = _require_string(current_mihomo.get("tag"), "resolved Mihomo tag")
    previous_version = _parse_semver(previous_tag, "locked Mihomo tag")
    current_version = _parse_semver(current_tag, "resolved Mihomo tag")
    if current_version < previous_version:
        raise SourceLockError(
            "Mihomo stable release downgrade rejected: {} -> {}".format(
                previous_tag, current_tag
            )
        )
    if current_version == previous_version and previous_mihomo != current_mihomo:
        raise SourceLockError(
            "Mihomo release {} changed without a version change".format(current_tag)
        )


def resolve_source_lock(client, previous=None):
    subconverter = _resolve_subconverter(client)
    mihomo = _resolve_mihomo(client)
    project = _resolve_project_identity()
    lock = {
        "mihomo": mihomo,
        "pair_id": _pair_id(subconverter, mihomo, project),
        "project": project,
        "schema_version": 1,
        "subconverter": subconverter,
    }
    _validate_transition(previous, lock)
    return lock


def load_lock(path):
    try:
        lock = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceLockError("unable to read existing source lock {}: {}".format(path, error))
    _validate_lock_integrity(lock)
    return lock


def render_lock(lock):
    return json.dumps(lock, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def write_lock(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(content)
            temporary_name = stream.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--api-base", default=DEFAULT_API_BASE)
    root.add_argument("--fixture-dir", help="Resolve offline from <dir>/responses.json")
    root.add_argument("--existing", help="Existing lock used for transition checks")
    root.add_argument("--output", default=str(DEFAULT_LOCK_PATH))
    root.add_argument("--check", action="store_true", help="Fail if --output differs")
    root.add_argument("--stdout", action="store_true", help="Print the resolved lock")
    return root


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        output_path = Path(args.output)
        existing_path = Path(args.existing) if args.existing else None
        if existing_path is None and output_path.exists():
            existing_path = output_path
        previous = load_lock(existing_path) if existing_path else None
        client = (
            FixtureGitHubClient(args.fixture_dir)
            if args.fixture_dir
            else GitHubClient(api_base=args.api_base)
        )
        lock = resolve_source_lock(client, previous=previous)
        rendered = render_lock(lock)

        if args.stdout:
            sys.stdout.write(rendered)
        if args.check:
            if not output_path.exists():
                raise SourceLockError("source lock {} does not exist".format(output_path))
            if output_path.read_text(encoding="utf-8") != rendered:
                raise SourceLockError("source lock {} is not current".format(output_path))
        elif not args.stdout:
            write_lock(output_path, rendered)
        return 0
    except SourceLockError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
