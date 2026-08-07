#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METADATA_PATH = ROOT / ".github" / "project-metadata.json"
SOURCE_LOCK_PATH = ROOT / ".github" / "source-lock.json"
TEMPLATE_PATH = ROOT / ".github" / "templates" / "README.md.tmpl"
README_PATH = ROOT / "README.md"


def load_metadata():
    with METADATA_PATH.open(encoding="utf-8") as stream:
        metadata = json.load(stream)

    required = {
        "upstream_repository",
        "upstream_branch",
        "upstream_version",
        "upstream_commit",
        "upstream_synced_at",
        "edition",
    }
    missing = sorted(required.difference(metadata))
    if missing:
        raise ValueError("missing metadata fields: " + ", ".join(missing))
    if not re.fullmatch(r"[0-9a-f]{40}", metadata["upstream_commit"]):
        raise ValueError("upstream_commit must be a full lowercase Git SHA")
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", metadata["upstream_version"]):
        raise ValueError("upstream_version must use vMAJOR.MINOR.PATCH")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", metadata["edition"]):
        raise ValueError("edition contains unsupported characters")

    version_header = (ROOT / "src" / "version.h").read_text(encoding="utf-8")
    version_match = re.search(r'^#define VERSION "([^"]+)"$', version_header, re.MULTILINE)
    if not version_match or version_match.group(1) != metadata["upstream_version"]:
        raise ValueError("upstream_version does not match src/version.h")
    return metadata


def load_source_lock():
    with SOURCE_LOCK_PATH.open(encoding="utf-8") as stream:
        source_lock = json.load(stream)

    required = {"schema_version", "mihomo", "pair_id", "project", "subconverter"}
    missing = sorted(required.difference(source_lock))
    if missing:
        raise ValueError("missing source lock fields: " + ", ".join(missing))
    if source_lock["schema_version"] != 1:
        raise ValueError("unsupported source lock schema_version")

    mihomo = source_lock["mihomo"]
    mihomo_required = {"repository", "release_url", "tag", "tag_identity"}
    missing = sorted(mihomo_required.difference(mihomo))
    if missing:
        raise ValueError("missing Mihomo source lock fields: " + ", ".join(missing))
    if mihomo["repository"] != "MetaCubeX/mihomo":
        raise ValueError("Mihomo source lock repository must be MetaCubeX/mihomo")
    if not isinstance(mihomo["tag"], str) or not re.fullmatch(
        r"v[0-9]+\.[0-9]+\.[0-9]+", mihomo["tag"]
    ):
        raise ValueError("Mihomo source lock tag must use vMAJOR.MINOR.PATCH")
    expected_release_url = "https://github.com/{}/releases/tag/{}".format(
        mihomo["repository"], mihomo["tag"]
    )
    if mihomo["release_url"] != expected_release_url:
        raise ValueError("Mihomo source lock release_url does not match tag")

    tag_identity = mihomo["tag_identity"]
    if not isinstance(tag_identity, dict) or not re.fullmatch(
        r"[0-9a-f]{40}", tag_identity.get("commit", "")
    ):
        raise ValueError("Mihomo source lock commit must be a full lowercase Git SHA")
    if not isinstance(source_lock["pair_id"], str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", source_lock["pair_id"]
    ):
        raise ValueError("source lock pair_id must be a SHA-256 identity")

    project = source_lock["project"]
    if not isinstance(project, dict):
        raise ValueError("source lock project identity must be an object")
    if project.get("parity_contract") != "mihomo-provider-fetch-v1":
        raise ValueError("unsupported Mihomo parity contract")
    if project.get("helper_protocol") != 1:
        raise ValueError("unsupported Mihomo helper protocol")
    return source_lock


def validate_source_lock(metadata, source_lock):
    locked_upstream = source_lock["subconverter"]
    expected = {
        "repository": metadata["upstream_repository"],
        "branch": metadata["upstream_branch"],
        "version": metadata["upstream_version"],
        "commit": metadata["upstream_commit"],
    }
    mismatches = [
        key for key, value in expected.items() if locked_upstream.get(key) != value
    ]
    if mismatches:
        raise ValueError(
            "source lock does not match project metadata: " + ", ".join(mismatches)
        )


def render(metadata, source_lock=None):
    if source_lock is None:
        source_lock = load_source_lock()
    validate_source_lock(metadata, source_lock)

    values = {key.upper(): str(value) for key, value in metadata.items()}
    values["UPSTREAM_COMMIT_SHORT"] = metadata["upstream_commit"][:8]
    mihomo = source_lock["mihomo"]
    values.update(
        {
            "MIHOMO_REPOSITORY": mihomo["repository"],
            "MIHOMO_RELEASE_URL": mihomo["release_url"],
            "MIHOMO_TAG": mihomo["tag"],
            "MIHOMO_COMMIT": mihomo["tag_identity"]["commit"],
            "MIHOMO_COMMIT_SHORT": mihomo["tag_identity"]["commit"][:8],
            "MIHOMO_PAIR_ID": source_lock["pair_id"],
            "MIHOMO_PARITY_CONTRACT": source_lock["project"]["parity_contract"],
            "MIHOMO_HELPER_PROTOCOL": str(source_lock["project"]["helper_protocol"]),
        }
    )
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    for key, value in values.items():
        content = content.replace("{{" + key + "}}", value)
    unresolved = sorted(set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", content)))
    if unresolved:
        raise ValueError("unresolved README placeholders: " + ", ".join(unresolved))
    return content


def git_project_commit():
    result = subprocess.run(
        ["git", "rev-parse", "--short=8", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def build_version(metadata, project_commit):
    project_commit = project_commit.strip()
    if not re.fullmatch(r"(?:[0-9a-fA-F]{7,40}|local)", project_commit):
        raise ValueError("project commit must be a Git SHA or local")
    return "{}-{}-{}.{}".format(
        metadata["upstream_version"],
        metadata["upstream_commit"][:8],
        metadata["edition"],
        project_commit[:8].lower(),
    )


def write_metadata(metadata):
    METADATA_PATH.write_text(
        json.dumps(metadata, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )


def command_render(args):
    metadata = load_metadata()
    expected = render(metadata)
    if args.check:
        actual = README_PATH.read_text(encoding="utf-8")
        if actual != expected:
            print("README.md is not synchronized with project metadata", file=sys.stderr)
            return 1
        return 0
    README_PATH.write_text(expected, encoding="utf-8")
    return 0


def command_version(args):
    metadata = load_metadata()
    project_commit = args.project_commit or git_project_commit()
    print(build_version(metadata, project_commit))
    return 0


def command_update(args):
    metadata = load_metadata()
    changed = False
    upstream_changed = False

    updates = {
        "upstream_version": args.upstream_version,
        "upstream_commit": args.upstream_commit,
    }
    for key, value in updates.items():
        if value is not None and metadata[key] != value:
            metadata[key] = value
            changed = True
            if key in {"upstream_version", "upstream_commit"}:
                upstream_changed = True

    if not changed:
        print("changed=false")
        return 0

    timestamp = args.timestamp or datetime.now().astimezone().isoformat(timespec="seconds")
    if upstream_changed:
        metadata["upstream_synced_at"] = timestamp

    rendered_readme = render(metadata)
    write_metadata(metadata)
    README_PATH.write_text(rendered_readme, encoding="utf-8")
    print("changed=true")
    return 0


def parser():
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)

    render_command = commands.add_parser("render")
    render_command.add_argument("--check", action="store_true")
    render_command.set_defaults(func=command_render)

    version_command = commands.add_parser("version")
    version_command.add_argument("--project-commit")
    version_command.set_defaults(func=command_version)

    update_command = commands.add_parser("update")
    update_command.add_argument("--upstream-version")
    update_command.add_argument("--upstream-commit")
    update_command.add_argument("--timestamp")
    update_command.set_defaults(func=command_update)

    return root


def main():
    try:
        args = parser().parse_args()
        return args.func(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
