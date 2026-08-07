#!/usr/bin/env python3

"""Build or install a source-locked subconverter Mihomo fetch helper."""

import argparse
import gzip
import hashlib
import json
import os
import platform as host_platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / ".github" / "source-lock.json"
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_mihomo_fetcher as helper_builder  # noqa: E402


class PackagingError(RuntimeError):
    pass


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _require_mapping(value, description):
    if not isinstance(value, dict):
        raise PackagingError("{} must be an object".format(description))
    return value


def _require_string(value, description):
    if not isinstance(value, str) or not value:
        raise PackagingError("{} must be a non-empty string".format(description))
    return value


def _validate_digest(value, description):
    value = _require_string(value, description)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise PackagingError("{} is not a SHA-256 identity".format(description))
    return value


def _validate_asset(value, description):
    asset = _require_mapping(value, description)
    required = {"digest", "download_url", "id", "name", "size"}
    if set(asset) != required:
        raise PackagingError("{} fields do not match the packaging contract".format(description))
    _validate_digest(asset["digest"], "{} digest".format(description))
    url = _require_string(asset["download_url"], "{} URL".format(description))
    if not url.startswith("https://github.com/"):
        raise PackagingError("{} URL must use GitHub HTTPS".format(description))
    if not isinstance(asset["id"], int) or asset["id"] <= 0:
        raise PackagingError("{} id must be a positive integer".format(description))
    name = _require_string(asset["name"], "{} name".format(description))
    if Path(name).name != name:
        raise PackagingError("{} name must not contain a path".format(description))
    if not isinstance(asset["size"], int) or asset["size"] <= 0:
        raise PackagingError("{} size must be a positive integer".format(description))


def load_and_validate_lock(path=DEFAULT_LOCK):
    try:
        lock = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PackagingError("unable to load source lock: {}".format(error))
    if lock.get("schema_version") != 1:
        raise PackagingError("unsupported source-lock schema")

    subconverter = _require_mapping(lock.get("subconverter"), "subconverter identity")
    mihomo = _require_mapping(lock.get("mihomo"), "Mihomo identity")
    project = _require_mapping(lock.get("project"), "project identity")
    expected_pair = "sha256:" + hashlib.sha256(
        canonical_bytes(
            {"mihomo": mihomo, "project": project, "subconverter": subconverter}
        )
    ).hexdigest()
    if lock.get("pair_id") != expected_pair:
        raise PackagingError("source-lock pair_id does not match its contents")

    _validate_digest(project.get("helper_overlay_sha256"), "helper overlay identity")
    if project.get("helper_protocol") != 1:
        raise PackagingError("unsupported helper protocol")
    _require_string(project.get("parity_contract"), "helper parity contract")

    repository = _require_string(mihomo.get("repository"), "Mihomo repository")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise PackagingError("Mihomo repository is not an owner/name pair")
    _require_string(mihomo.get("tag"), "Mihomo tag")
    tag_identity = _require_mapping(mihomo.get("tag_identity"), "Mihomo tag identity")
    if not re.fullmatch(r"[0-9a-f]{40}", str(tag_identity.get("commit", ""))):
        raise PackagingError("Mihomo commit is not a full Git object id")
    if not re.fullmatch(r"[0-9a-f]{40}", str(tag_identity.get("tree", ""))):
        raise PackagingError("Mihomo tree is not a full Git object id")

    assets = _require_mapping(mihomo.get("required_assets"), "required Mihomo assets")
    if set(assets) != {"toolchain", "vendor"}:
        raise PackagingError("required Mihomo assets must be toolchain and vendor")
    _validate_asset(assets["toolchain"], "locked toolchain")
    _validate_asset(assets["vendor"], "locked vendor")
    oracles = _require_mapping(mihomo.get("oracle_assets"), "Mihomo oracle assets")
    expected_oracles = set(helper_builder.PLATFORM_TARGETS)
    if set(oracles) != expected_oracles:
        raise PackagingError("Mihomo oracle assets do not cover every helper platform")
    for target in sorted(expected_oracles):
        _validate_asset(oracles[target], "locked {} oracle".format(target))

    try:
        helper_builder.load_lock(path)
    except helper_builder.BuildError as error:
        raise PackagingError(str(error))
    return lock


def helper_name(target):
    if target not in helper_builder.PLATFORM_TARGETS:
        raise PackagingError("unsupported helper platform {}".format(target))
    suffix = ".exe" if target.startswith("windows-") else ""
    return "subconverter-mihomo-fetcher" + suffix


def _validate_binary_platform(binary, target):
    path = Path(binary)
    try:
        with path.open("rb") as stream:
            header = stream.read(64)
            if target.startswith("linux-"):
                if len(header) < 20 or header[:4] != b"\x7fELF" or header[5] != 1:
                    raise PackagingError("helper is not a little-endian ELF executable")
                expected_class = 1 if target in {"linux-386", "linux-armv7"} else 2
                if header[4] != expected_class:
                    raise PackagingError("helper ELF class does not match {}".format(target))
                machine = int.from_bytes(header[18:20], "little")
                expected = {
                    "linux-386": 3,
                    "linux-amd64": 62,
                    "linux-armv7": 40,
                    "linux-arm64": 183,
                }[target]
                if machine != expected:
                    raise PackagingError("helper ELF architecture does not match {}".format(target))
                return
            if target.startswith("windows-"):
                if len(header) < 64 or header[:2] != b"MZ":
                    raise PackagingError("helper is not a PE executable")
                pe_offset = int.from_bytes(header[60:64], "little")
                stream.seek(pe_offset)
                pe_header = stream.read(6)
                if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
                    raise PackagingError("helper has an invalid PE header")
                machine = int.from_bytes(pe_header[4:6], "little")
                expected = {"windows-386": 0x14C, "windows-amd64": 0x8664}[target]
                if machine != expected:
                    raise PackagingError("helper PE architecture does not match {}".format(target))
                return
            if len(header) < 8 or header[:4] != b"\xcf\xfa\xed\xfe":
                raise PackagingError("helper is not a little-endian 64-bit Mach-O executable")
            cpu_type = int.from_bytes(header[4:8], "little")
            expected = {"macos-amd64": 0x01000007, "macos-arm64": 0x0100000C}[target]
            if cpu_type != expected:
                raise PackagingError("helper Mach-O architecture does not match {}".format(target))
    except OSError as error:
        raise PackagingError("unable to inspect helper executable: {}".format(error))


CERTIFICATE_RUN = re.compile(
    rb"(?:-----BEGIN CERTIFICATE-----\r?\n"
    rb"(?:[A-Za-z0-9+/=]+\r?\n)+"
    rb"-----END CERTIFICATE-----\r?\n)+"
)


def extract_embedded_ca(binary):
    try:
        content = Path(binary).read_bytes()
    except OSError as error:
        raise PackagingError("unable to inspect embedded CA bundle: {}".format(error))
    runs = list(CERTIFICATE_RUN.finditer(content))
    if not runs:
        raise PackagingError("helper executable contains no embedded CA bundle")
    largest_size = max(len(match.group(0)) for match in runs)
    largest_runs = [match.group(0) for match in runs if len(match.group(0)) == largest_size]
    if len(largest_runs) != 1:
        raise PackagingError("executable does not contain one unique largest CA bundle")
    largest = largest_runs[0]
    certificate_count = largest.count(b"-----BEGIN CERTIFICATE-----")
    if certificate_count < 50:
        raise PackagingError("helper executable lacks a complete embedded CA bundle")
    return largest


def ca_identity(content):
    return {
        "certificate_count": content.count(b"-----BEGIN CERTIFICATE-----"),
        "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        "size": len(content),
    }


def manifest_for(lock, target, binary):
    binary = Path(binary)
    if not binary.is_file() or binary.stat().st_size <= 0:
        raise PackagingError("helper binary is missing or empty")
    _validate_binary_platform(binary, target)
    embedded_ca = extract_embedded_ca(binary)
    mihomo = lock["mihomo"]
    project = lock["project"]
    return {
        "helper": {
            "name": helper_name(target),
            "sha256": sha256_file(binary),
            "size": binary.stat().st_size,
        },
        "inputs": {
            "ca_oracle": mihomo["oracle_assets"][target],
            "toolchain": mihomo["required_assets"]["toolchain"],
            "vendor": mihomo["required_assets"]["vendor"],
        },
        "embedded_ca": ca_identity(embedded_ca),
        "mihomo": {
            "commit": mihomo["tag_identity"]["commit"],
            "repository": mihomo["repository"],
            "tag": mihomo["tag"],
            "tree": mihomo["tag_identity"]["tree"],
        },
        "pair_id": lock["pair_id"],
        "platform": target,
        "project": {
            "helper_overlay_sha256": project["helper_overlay_sha256"],
            "helper_protocol": project["helper_protocol"],
            "parity_contract": project["parity_contract"],
        },
        "schema_version": 1,
    }


def _validate_file(path, asset, description):
    path = Path(path)
    if not path.is_file():
        raise PackagingError("{} is missing".format(description))
    actual_size = path.stat().st_size
    if actual_size != asset["size"]:
        raise PackagingError(
            "{} size mismatch: {} != {}".format(description, actual_size, asset["size"])
        )
    actual_digest = sha256_file(path)
    if actual_digest != asset["digest"]:
        raise PackagingError(
            "{} digest mismatch: {} != {}".format(
                description, actual_digest, asset["digest"]
            )
        )


def _download_asset(asset, cache_dir, description):
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / asset["name"]
    if destination.exists():
        _validate_file(destination, asset, description)
        return destination

    temporary = cache_dir / (asset["name"] + ".part-{}".format(os.getpid()))
    request = urllib.request.Request(
        asset["download_url"], headers={"User-Agent": "subconverter-source-lock-builder/1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        _validate_file(temporary, asset, description)
        os.replace(temporary, destination)
    except (OSError, urllib.error.URLError, PackagingError) as error:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise PackagingError("unable to obtain {}: {}".format(description, error))
    return destination


def _extract_locked_archive(archive_path, destination, description):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            root = destination.resolve()
            for member in members:
                if member.issym() or member.islnk() or member.isdev():
                    raise PackagingError("{} contains an unsupported link or device".format(description))
                target = (destination / member.name).resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    raise PackagingError("{} contains a path outside its root".format(description))
            for member in members:
                archive.extract(member, destination)
    except (OSError, tarfile.TarError) as error:
        raise PackagingError("unable to extract {}: {}".format(description, error))


def _oracle_executable(archive_path, asset):
    try:
        if asset["name"].endswith(".gz"):
            content = gzip.decompress(Path(archive_path).read_bytes())
        elif asset["name"].endswith(".zip"):
            with zipfile.ZipFile(archive_path) as archive:
                files = [entry for entry in archive.infolist() if not entry.is_dir()]
                if len(files) != 1:
                    raise PackagingError("locked Mihomo oracle ZIP must contain one executable")
                if files[0].file_size <= 0 or files[0].file_size > 256 * 1024 * 1024:
                    raise PackagingError("locked Mihomo oracle executable size is invalid")
                content = archive.read(files[0])
        else:
            raise PackagingError("locked Mihomo oracle has an unsupported archive type")
    except (OSError, gzip.BadGzipFile, zipfile.BadZipFile) as error:
        raise PackagingError("unable to unpack locked Mihomo oracle: {}".format(error))
    if not content or len(content) > 256 * 1024 * 1024:
        raise PackagingError("locked Mihomo oracle executable size is invalid")
    return content


def extract_official_ca(archive_path, asset):
    with tempfile.TemporaryDirectory(prefix="subconverter-mihomo-oracle-") as directory:
        executable = Path(directory) / "mihomo-oracle"
        executable.write_bytes(_oracle_executable(archive_path, asset))
        return extract_embedded_ca(executable)


def _run(command, cwd=None, env=None, description="command"):
    try:
        return subprocess.run(
            [str(item) for item in command],
            cwd=cwd,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise PackagingError("{} failed: {}".format(description, error))


def _checkout_source(lock, destination):
    repository = lock["mihomo"]["repository"]
    identity = lock["mihomo"]["tag_identity"]
    url = "https://github.com/{}.git".format(repository)
    destination = Path(destination)
    destination.mkdir(parents=True)
    _run(["git", "init", "--quiet", destination], description="Mihomo Git init")
    _run(
        ["git", "-C", destination, "remote", "add", "origin", url],
        description="Mihomo Git remote setup",
    )
    _run(
        [
            "git",
            "-C",
            destination,
            "fetch",
            "--quiet",
            "--depth=1",
            "origin",
            identity["commit"],
        ],
        description="locked Mihomo source fetch",
    )
    _run(
        ["git", "-C", destination, "checkout", "--quiet", "--detach", "FETCH_HEAD"],
        description="locked Mihomo checkout",
    )
    commit = _run(
        ["git", "-C", destination, "rev-parse", "HEAD"],
        description="Mihomo commit verification",
    ).stdout.strip()
    tree = _run(
        ["git", "-C", destination, "rev-parse", "HEAD^{tree}"],
        description="Mihomo tree verification",
    ).stdout.strip()
    if commit != identity["commit"] or tree != identity["tree"]:
        raise PackagingError("checked-out Mihomo source does not match the source lock")
    status = _run(
        ["git", "-C", destination, "status", "--porcelain", "--untracked-files=all"],
        description="fresh Mihomo checkout verification",
    ).stdout
    if status:
        raise PackagingError("fresh Mihomo checkout is unexpectedly dirty")


def _locked_go(toolchain_root):
    go = Path(toolchain_root) / "go" / "bin" / "go"
    version_file = Path(toolchain_root) / "go" / "VERSION"
    if not go.is_file() or not version_file.is_file():
        raise PackagingError("locked toolchain archive lacks go/bin/go or go/VERSION")
    go.chmod(go.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    expected = version_file.read_text(encoding="utf-8").splitlines()[0].strip()
    if not re.fullmatch(r"go[0-9]+\.[0-9]+(?:\.[0-9]+)?", expected):
        raise PackagingError("locked toolchain VERSION is invalid")
    reported = _run([go, "version"], description="locked Go toolchain probe").stdout.strip()
    if " {} ".format(expected) not in " {} ".format(reported):
        raise PackagingError("locked Go executable does not match its VERSION file")
    return go


def build_locked(args):
    if sys.platform != "linux" or host_platform.machine().lower() not in {
        "x86_64",
        "amd64",
    }:
        raise PackagingError(
            "the official locked toolchain is Linux/amd64; build helpers on that host"
        )
    lock = load_and_validate_lock(args.lock)
    assets = lock["mihomo"]["required_assets"]

    cache_context = None
    if args.cache_dir:
        cache_dir = Path(args.cache_dir).resolve()
    else:
        cache_context = tempfile.TemporaryDirectory(prefix="subconverter-mihomo-assets-")
        cache_dir = Path(cache_context.name)
    try:
        toolchain_archive = _download_asset(
            assets["toolchain"], cache_dir, "locked Mihomo toolchain"
        )
        vendor_archive = _download_asset(assets["vendor"], cache_dir, "locked Mihomo vendor")
        oracle_asset = lock["mihomo"]["oracle_assets"][args.platform]
        oracle_archive = _download_asset(
            oracle_asset, cache_dir, "locked {} Mihomo oracle".format(args.platform)
        )
        official_ca = extract_official_ca(oracle_archive, oracle_asset)
        with tempfile.TemporaryDirectory(prefix="subconverter-mihomo-package-") as directory:
            work = Path(directory)
            source = work / "source"
            toolchain = work / "toolchain"
            dependencies = work / "dependencies"
            temporary_binary = work / helper_name(args.platform)
            _checkout_source(lock, source)
            ca_destination = source / "component" / "ca" / "ca-certificates.crt"
            if not ca_destination.is_file():
                raise PackagingError("locked Mihomo source lacks its embedded CA destination")
            ca_bundle = work / "locked-ca-certificates.crt"
            ca_bundle.write_bytes(official_ca)
            _extract_locked_archive(toolchain_archive, toolchain, "locked Mihomo toolchain")
            _extract_locked_archive(vendor_archive, dependencies, "locked Mihomo vendor")
            go = _locked_go(toolchain)
            vendor = dependencies / "vendor"
            if not vendor.is_dir():
                raise PackagingError("locked vendor archive lacks its vendor directory")
            build_args = argparse.Namespace(
                go=str(go),
                lock=str(Path(args.lock).resolve()),
                mihomo_source=str(source),
                output=str(temporary_binary),
                platform=args.platform,
                vendor=str(vendor),
                ca_bundle=str(ca_bundle),
            )
            try:
                helper_builder.build(build_args)
            except helper_builder.BuildError as error:
                raise PackagingError(str(error))

            output = Path(args.output).resolve()
            manifest_path = Path(args.manifest).resolve()
            if output == manifest_path:
                raise PackagingError("helper binary and manifest destinations must differ")
            if output.exists() or manifest_path.exists():
                raise PackagingError("helper build destinations must be empty")
            output.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest = manifest_for(lock, args.platform, temporary_binary)
            if manifest["embedded_ca"] != ca_identity(official_ca):
                raise PackagingError("helper embedded CA does not match the locked Mihomo oracle")
            temporary_output = output.with_name(output.name + ".building")
            temporary_manifest = manifest_path.with_name(manifest_path.name + ".building")
            try:
                shutil.copy2(temporary_binary, temporary_output)
                temporary_output.chmod(
                    temporary_output.stat().st_mode
                    | stat.S_IXUSR
                    | stat.S_IXGRP
                    | stat.S_IXOTH
                )
                temporary_manifest.write_text(
                    json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                if manifest_for(lock, args.platform, temporary_output) != manifest:
                    raise PackagingError("installed helper changed after the locked build")
                os.replace(temporary_manifest, manifest_path)
                try:
                    os.replace(temporary_output, output)
                except OSError:
                    manifest_path.unlink(missing_ok=True)
                    raise
            finally:
                temporary_output.unlink(missing_ok=True)
                temporary_manifest.unlink(missing_ok=True)
    finally:
        if cache_context is not None:
            cache_context.cleanup()


def install_locked(args):
    lock = load_and_validate_lock(args.lock)
    binary = Path(args.binary).resolve()
    try:
        supplied_manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PackagingError("unable to load helper manifest: {}".format(error))
    expected_manifest = manifest_for(lock, args.platform, binary)
    if supplied_manifest != expected_manifest:
        raise PackagingError("helper manifest does not match its binary and current source lock")

    destination = Path(args.destination).resolve()
    manifest_destination = Path(args.manifest_destination).resolve()
    if destination == manifest_destination:
        raise PackagingError("helper binary and manifest destinations must differ")
    if destination.exists() or manifest_destination.exists():
        raise PackagingError("helper package destinations must be empty before installation")
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_binary = destination.with_name(destination.name + ".installing")
    temporary_manifest = manifest_destination.with_name(
        manifest_destination.name + ".installing"
    )
    try:
        shutil.copy2(binary, temporary_binary)
        temporary_binary.chmod(
            temporary_binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        shutil.copy2(args.manifest, temporary_manifest)
        if manifest_for(lock, args.platform, temporary_binary) != supplied_manifest:
            raise PackagingError("helper changed while it was being installed")
        os.replace(temporary_manifest, manifest_destination)
        try:
            os.replace(temporary_binary, destination)
        except OSError:
            manifest_destination.unlink(missing_ok=True)
            raise
    finally:
        for temporary in (temporary_binary, temporary_manifest):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    subcommands = root.add_subparsers(dest="command", required=True)

    build = subcommands.add_parser(
        "build", help="build one target with the exact locked Linux toolchain and vendor"
    )
    build.add_argument("--lock", default=str(DEFAULT_LOCK))
    build.add_argument("--platform", required=True, choices=sorted(helper_builder.PLATFORM_TARGETS))
    build.add_argument("--output", required=True)
    build.add_argument("--manifest", required=True)
    build.add_argument("--cache-dir")
    build.set_defaults(handler=build_locked)

    install = subcommands.add_parser(
        "install", help="verify and install a prebuilt helper/manifest pair"
    )
    install.add_argument("--lock", default=str(DEFAULT_LOCK))
    install.add_argument(
        "--platform", required=True, choices=sorted(helper_builder.PLATFORM_TARGETS)
    )
    install.add_argument("--binary", required=True)
    install.add_argument("--manifest", required=True)
    install.add_argument("--destination", required=True)
    install.add_argument("--manifest-destination", required=True)
    install.set_defaults(handler=install_locked)
    return root


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        args.handler(args)
        return 0
    except PackagingError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
