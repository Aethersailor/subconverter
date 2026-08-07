#!/usr/bin/env python3

"""Build the companion fetcher inside an exact locked Mihomo source tree."""

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / ".github" / "source-lock.json"
OVERLAY_FILES = (
    Path("mihomo-fetcher/cmd/subconverter-mihomo-fetcher/main.go"),
)
TEST_OVERLAY_FILES = (
    Path("mihomo-fetcher/cmd/subconverter-mihomo-fetcher/main_test.go"),
)

PLATFORM_TARGETS = {
    "linux-386": ("linux", "386", {"GO386": "sse2"}),
    "linux-amd64": ("linux", "amd64", {"GOAMD64": "v1"}),
    "linux-armv7": ("linux", "arm", {"GOARM": "7"}),
    "linux-arm64": ("linux", "arm64", {}),
    "macos-amd64": ("darwin", "amd64", {"GOAMD64": "v1"}),
    "macos-arm64": ("darwin", "arm64", {}),
    "windows-386": ("windows", "386", {"GO386": "sse2"}),
    "windows-amd64": ("windows", "amd64", {"GOAMD64": "v1"}),
}


class BuildError(RuntimeError):
    pass


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def overlay_digest(root=ROOT):
    identities = {}
    for relative in OVERLAY_FILES:
        path = root / relative
        try:
            content = path.read_bytes()
        except OSError as error:
            raise BuildError("unable to read helper overlay {}: {}".format(path, error))
        identities[relative.as_posix()] = hashlib.sha256(content).hexdigest()
    return "sha256:" + hashlib.sha256(canonical_bytes(identities)).hexdigest()


def load_lock(path=DEFAULT_LOCK):
    try:
        lock = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuildError("unable to load source lock: {}".format(error))
    try:
        project = lock["project"]
        mihomo = lock["mihomo"]
        version = mihomo["tag"]
        commit = mihomo["tag_identity"]["commit"]
        expected_overlay = project["helper_overlay_sha256"]
        protocol = project["helper_protocol"]
    except (KeyError, TypeError) as error:
        raise BuildError("source lock lacks helper identity: {}".format(error))
    if protocol != 1:
        raise BuildError("unsupported helper protocol {}".format(protocol))
    actual_overlay = overlay_digest()
    if expected_overlay != actual_overlay:
        raise BuildError(
            "helper overlay does not match source lock: {} != {}".format(
                actual_overlay, expected_overlay
            )
        )
    return lock, version, commit, actual_overlay


def git_identity(source):
    try:
        result = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BuildError("Mihomo source must be a Git checkout: {}".format(error))
    values = result.stdout.splitlines()
    if len(values) != 2:
        raise BuildError("Mihomo Git checkout returned an invalid identity")
    return values[0].strip(), values[1].strip()


def require_clean_source(source, commit, tree):
    actual_commit, actual_tree = git_identity(source)
    if actual_commit != commit or actual_tree != tree:
        raise BuildError("Mihomo checkout does not match the locked commit and tree")
    try:
        status = subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "status",
                "--porcelain",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise BuildError("unable to verify Mihomo source cleanliness: {}".format(error))
    if status:
        raise BuildError("Mihomo checkout must be completely clean")


def copy_source(source, destination, ca_bundle):
    try:
        archive = subprocess.run(
            ["git", "-C", str(source), "archive", "--format=tar", "HEAD"],
            check=True,
            capture_output=True,
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
            members = stream.getmembers()
            root = destination.resolve()
            for member in members:
                if member.issym() or member.islnk() or member.isdev():
                    raise BuildError("locked Mihomo source archive contains a link or device")
                target = (destination / member.name).resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    raise BuildError("locked Mihomo source archive escapes its root")
            destination.mkdir(parents=True)
            try:
                stream.extractall(destination, filter="fully_trusted")
            except TypeError:
                # Compatibility with Python versions predating extraction filters;
                # every member was already validated above.
                stream.extractall(destination)
    except (OSError, subprocess.CalledProcessError, tarfile.TarError) as error:
        raise BuildError("unable to export locked Mihomo source: {}".format(error))

    ca_destination = destination / "component" / "ca" / "ca-certificates.crt"
    try:
        ca_destination.write_bytes(Path(ca_bundle).read_bytes())
    except OSError as error:
        raise BuildError("unable to inject the locked CA bundle: {}".format(error))
    for relative in OVERLAY_FILES + TEST_OVERLAY_FILES:
        overlay_source = ROOT / relative
        overlay_destination = (
            destination / "cmd" / "subconverter-mihomo-fetcher" / relative.name
        )
        overlay_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(overlay_source, overlay_destination)


def build(args):
    lock, version, commit, digest = load_lock(args.lock)
    source = Path(args.mihomo_source).resolve()
    tree = lock["mihomo"]["tag_identity"]["tree"]
    require_clean_source(source, commit, tree)
    if args.platform not in PLATFORM_TARGETS:
        raise BuildError("unsupported platform {}".format(args.platform))
    goos, goarch, target_env = PLATFORM_TARGETS[args.platform]
    vendor = Path(args.vendor).resolve()
    if not vendor.is_dir():
        raise BuildError("--vendor must point to the extracted locked vendor directory")
    ca_bundle = Path(args.ca_bundle).resolve()
    if not ca_bundle.is_file() or ca_bundle.stat().st_size == 0:
        raise BuildError("--ca-bundle must point to the extracted locked CA bundle")
    go_binary = Path(args.go).resolve()
    if not go_binary.is_file():
        raise BuildError("--go must point to the extracted locked Go executable")

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="subconverter-mihomo-build-") as directory:
        build_root = Path(directory) / "mihomo"
        copy_source(source, build_root, ca_bundle)
        shutil.copytree(vendor, build_root / "vendor")

        environment = os.environ.copy()
        for name in tuple(environment):
            if name.startswith("GO") or name.startswith("CGO_"):
                environment.pop(name, None)
        environment.update(
            {
                "CGO_ENABLED": "0",
                "GOOS": goos,
                "GOARCH": goarch,
                "GOENV": "off",
                "GOPROXY": "off",
                "GOSUMDB": "off",
                "GOTOOLCHAIN": "local",
            }
        )
        environment.update(target_env)
        environment["GOFLAGS"] = "-mod=vendor"

        published_at = lock["mihomo"]["published_at"]
        ldflags = " ".join(
            [
                "-X github.com/metacubex/mihomo/constant.Version={}".format(version),
                "-X github.com/metacubex/mihomo/constant.BuildTime={}".format(published_at),
                "-X main.mihomoCommit={}".format(commit),
                "-X main.overlayHash={}".format(digest),
                "-w",
                "-s",
                "-buildid=",
            ]
        )
        if args.platform == "linux-amd64":
            try:
                subprocess.run(
                    [
                        str(go_binary),
                        "test",
                        "-count=1",
                        "-tags",
                        "with_gvisor",
                        "./cmd/subconverter-mihomo-fetcher",
                    ],
                    cwd=build_root,
                    env=environment,
                    check=True,
                )
            except (OSError, subprocess.CalledProcessError) as error:
                raise BuildError("Mihomo helper tests failed: {}".format(error))

        command = [
            str(go_binary),
            "build",
            "-tags",
            "with_gvisor",
            "-trimpath",
            "-ldflags",
            ldflags,
            "-o",
            str(output),
            "./cmd/subconverter-mihomo-fetcher",
        ]
        try:
            subprocess.run(command, cwd=build_root, env=environment, check=True)
        except (OSError, subprocess.CalledProcessError) as error:
            raise BuildError("Mihomo helper build failed: {}".format(error))

    if not output.is_file() or output.stat().st_size == 0:
        raise BuildError("Mihomo helper build produced no executable")


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--lock", default=str(DEFAULT_LOCK))
    root.add_argument("--mihomo-source", required=True)
    root.add_argument("--platform", required=True, choices=sorted(PLATFORM_TARGETS))
    root.add_argument("--output", required=True)
    root.add_argument("--go", required=True, help="Extracted locked Go binary")
    root.add_argument("--vendor", required=True, help="Extracted locked vendor directory")
    root.add_argument("--ca-bundle", required=True, help="CA bytes extracted from the locked oracle")
    return root


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        build(args)
        return 0
    except BuildError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
