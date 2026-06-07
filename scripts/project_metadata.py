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
        "user_agent",
        "user_agent_source",
        "user_agent_updated_at",
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


def render(metadata):
    values = {key.upper(): str(value) for key, value in metadata.items()}
    values["UPSTREAM_COMMIT_SHORT"] = metadata["upstream_commit"][:8]
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
    user_agent_changed = False

    updates = {
        "upstream_version": args.upstream_version,
        "upstream_commit": args.upstream_commit,
        "user_agent": args.user_agent,
    }
    for key, value in updates.items():
        if value is not None and metadata[key] != value:
            metadata[key] = value
            changed = True
            if key in {"upstream_version", "upstream_commit"}:
                upstream_changed = True
            elif key == "user_agent":
                user_agent_changed = True

    if not changed:
        print("changed=false")
        return 0

    timestamp = args.timestamp or datetime.now().astimezone().isoformat(timespec="seconds")
    if upstream_changed:
        metadata["upstream_synced_at"] = timestamp
    if user_agent_changed:
        metadata["user_agent_updated_at"] = timestamp

    write_metadata(metadata)
    README_PATH.write_text(render(metadata), encoding="utf-8")
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
    update_command.add_argument("--user-agent")
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
