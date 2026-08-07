import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MihomoHelperIdentityTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "requires a POSIX shell")
    def test_explicit_cross_arch_platform_wins_over_kernel_arch(self):
        installer = ROOT / "scripts" / "install_locked_mihomo_fetcher.sh"
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fake_bin = work / "bin"
            fake_bin.mkdir()
            fake_python = fake_bin / "python3"
            fake_python.write_text(
                """#!/bin/sh
shift
while [ \"$#\" -gt 0 ]; do
    case \"$1\" in
        --platform) platform=\"$2\"; shift 2 ;;
        --destination) destination=\"$2\"; shift 2 ;;
        --manifest-destination) manifest=\"$2\"; shift 2 ;;
        *) shift ;;
    esac
done
printf '%s' \"$platform\" > \"$destination\"
printf 'verified' > \"$manifest\"
""",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            helper = work / "helper"
            manifest = work / "manifest.json"
            helper.write_bytes(b"helper")
            manifest.write_text("{}", encoding="utf-8")

            for platform in ("linux-386", "linux-armv7"):
                with self.subTest(platform=platform):
                    destination = work / platform
                    environment = os.environ.copy()
                    environment.pop("PYTHON_BIN", None)
                    environment.update(
                        {
                            "PATH": str(fake_bin) + os.pathsep + environment["PATH"],
                            "SUBCONVERTER_MIHOMO_FETCHER_BIN": str(helper),
                            "SUBCONVERTER_MIHOMO_FETCHER_MANIFEST": str(manifest),
                            "SUBCONVERTER_MIHOMO_FETCHER_PLATFORM": platform,
                        }
                    )
                    subprocess.run(
                        ["bash", str(installer), str(destination)],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=environment,
                    )
                    self.assertEqual(
                        (destination / "subconverter-mihomo-fetcher").read_text(),
                        platform,
                    )

    def test_installer_rejects_unknown_explicit_platform_before_verification(self):
        source = (ROOT / "scripts/install_locked_mihomo_fetcher.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('case "$helper_platform" in', source)
        self.assertIn("unsupported Mihomo helper platform", source)
        self.assertNotIn("override does not match the packaging host", source)

    def test_runtime_verifies_manifest_and_baked_binary_identity_before_spawn(self):
        source = (ROOT / "src/handler/mihomo_fetch_client.cpp").read_text(encoding="utf-8")
        validation = source.index("validateHelperIdentity(*helper, *manifest)")
        spawn = source.index("process_.start(*helper)")
        self.assertLess(validation, spawn)
        for identity in (
            "schema_version",
            "pair_id",
            "platform",
            "helper_overlay_sha256",
            "helper_protocol",
            "parity_contract",
            "SUBCONVERTER_MIHOMO_HELPER_SHA256",
            "file_size(helper",
            "sha256File(helper)",
            "manifest_size > max_manifest_size",
        ):
            self.assertIn(identity, source)

    def test_unbound_development_build_fails_closed(self):
        source = (ROOT / "src/handler/mihomo_fetch_client.cpp").read_text(encoding="utf-8")
        self.assertIn("expected_digest.empty()", source)
        self.assertIn("expected_platform.empty()", source)
        self.assertIn("expected_name.empty()", source)
        template = (ROOT / "cmake/project_version.h.in").read_text(encoding="utf-8")
        self.assertIn("SUBCONVERTER_MIHOMO_HELPER_SHA256", template)

    def test_hello_checks_target_toolchain_and_exact_capabilities(self):
        source = (ROOT / "src/handler/mihomo_fetch_client.cpp").read_text(encoding="utf-8")
        for field in ("go_version", "goos", "goarch", "capabilities"):
            self.assertIn('"{}"'.format(field), source)
        capability_match = re.search(
            r"static const std::set<std::string> expected = \{(.*?)\};",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(capability_match)
        capabilities = set(re.findall(r'"([a-z0-9-]+)"', capability_match.group(1)))
        self.assertEqual(
            capabilities,
            {
                "direct",
                "http-proxy",
                "https-proxy",
                "socks5-proxy",
                "etag",
                "raw-body",
                "response-headers",
            },
        )
        self.assertIn("value.size() != expected.size()", source)

    def test_process_creation_uses_explicit_descriptor_and_handle_boundaries(self):
        source = (ROOT / "src/handler/mihomo_fetch_client.cpp").read_text(encoding="utf-8")
        self.assertIn("posix_spawn(", source)
        self.assertNotIn("fork();", source)
        self.assertNotIn("execl(", source)
        self.assertIn("FD_CLOEXEC", source)
        self.assertIn("SIGTERM", source)
        self.assertIn("SIGKILL", source)
        self.assertIn("PROC_THREAD_ATTRIBUTE_HANDLE_LIST", source)
        self.assertIn("EXTENDED_STARTUPINFO_PRESENT", source)

    def test_every_release_path_binds_the_pair_into_cmake(self):
        for relative in (
            "scripts/build.alpine.release.sh",
            "scripts/build.macos.release.sh",
            "scripts/build.windows.release.sh",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            for argument in (
                "SUBCONVERTER_MIHOMO_FETCHER_BINARY",
                "SUBCONVERTER_MIHOMO_FETCHER_MANIFEST",
                "SUBCONVERTER_MIHOMO_FETCHER_PLATFORM",
            ):
                self.assertIn("-D{}=".format(argument), source, relative)

        dockerfile = (ROOT / "scripts/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY --from=mihomo-fetcher-builder", dockerfile)
        self.assertIn("-DSUBCONVERTER_MIHOMO_FETCHER_BINARY=", dockerfile)
        self.assertIn("-DSUBCONVERTER_MIHOMO_FETCHER_MANIFEST=", dockerfile)
        self.assertIn("-DSUBCONVERTER_MIHOMO_FETCHER_PLATFORM=", dockerfile)
        self.assertIn("SUBCONVERTER_MIHOMO_FETCHER_MANIFEST_PATH=", dockerfile)


if __name__ == "__main__":
    unittest.main()
