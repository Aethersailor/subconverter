import importlib.util
import json
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "package_mihomo_fetcher.py"
SPEC = importlib.util.spec_from_file_location("package_mihomo_fetcher", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MihomoFetcherPackagingTests(unittest.TestCase):
    def setUp(self):
        self.lock_path = Path(
            os.environ.get("SUBCONVERTER_SOURCE_LOCK", str(MODULE.DEFAULT_LOCK))
        )
        self.lock = MODULE.load_and_validate_lock(self.lock_path)

    @staticmethod
    def elf(machine):
        header = bytearray(64)
        header[:4] = b"\x7fELF"
        header[4] = 2
        header[5] = 1
        header[18:20] = machine.to_bytes(2, "little")
        certificate = (
            b"-----BEGIN CERTIFICATE-----\n"
            b"QUJD\n"
            b"-----END CERTIFICATE-----\n"
        )
        return bytes(header) + certificate * 50

    def test_manifest_records_all_locked_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "subconverter-mihomo-fetcher"
            binary.write_bytes(self.elf(62))
            manifest = MODULE.manifest_for(self.lock, "linux-amd64", binary)
            self.assertEqual(manifest["pair_id"], self.lock["pair_id"])
            self.assertEqual(
                manifest["inputs"]["toolchain"],
                self.lock["mihomo"]["required_assets"]["toolchain"],
            )
            self.assertEqual(
                manifest["inputs"]["ca_oracle"],
                self.lock["mihomo"]["oracle_assets"]["linux-amd64"],
            )
            self.assertEqual(
                manifest["project"]["helper_overlay_sha256"],
                self.lock["project"]["helper_overlay_sha256"],
            )

    def test_install_accepts_only_matching_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "input-helper"
            manifest_path = root / "input-manifest.json"
            destination = root / "package" / "subconverter-mihomo-fetcher"
            destination_manifest = root / "package" / "manifest.json"
            binary.write_bytes(self.elf(62))
            manifest_path.write_text(
                json.dumps(MODULE.manifest_for(self.lock, "linux-amd64", binary)),
                encoding="utf-8",
            )
            MODULE.install_locked(
                Namespace(
                    binary=str(binary),
                    destination=str(destination),
                    lock=str(self.lock_path),
                    manifest=str(manifest_path),
                    manifest_destination=str(destination_manifest),
                    platform="linux-amd64",
                )
            )
            self.assertEqual(destination.read_bytes(), self.elf(62))
            if os.name != "nt":
                self.assertTrue(destination.stat().st_mode & 0o111)
            self.assertEqual(
                json.loads(destination_manifest.read_text(encoding="utf-8")),
                MODULE.manifest_for(self.lock, "linux-amd64", destination),
            )

    def test_install_rejects_tampered_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "input-helper"
            manifest_path = root / "input-manifest.json"
            binary.write_bytes(self.elf(62))
            manifest_path.write_text(
                json.dumps(MODULE.manifest_for(self.lock, "linux-amd64", binary)),
                encoding="utf-8",
            )
            binary.write_bytes(self.elf(62) + b"tampered")
            with self.assertRaises(MODULE.PackagingError):
                MODULE.install_locked(
                    Namespace(
                        binary=str(binary),
                        destination=str(root / "output"),
                        lock=str(self.lock_path),
                        manifest=str(manifest_path),
                        manifest_destination=str(root / "output.json"),
                        platform="linux-amd64",
                    )
                )

    def test_install_rejects_wrong_platform(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "input-helper.exe"
            manifest_path = root / "input-manifest.json"
            pe = bytearray(128)
            pe[:2] = b"MZ"
            pe[60:64] = (64).to_bytes(4, "little")
            pe[64:68] = b"PE\0\0"
            pe[68:70] = (0x8664).to_bytes(2, "little")
            certificate = (
                b"-----BEGIN CERTIFICATE-----\n"
                b"QUJD\n"
                b"-----END CERTIFICATE-----\n"
            )
            pe.extend(certificate * 50)
            binary.write_bytes(pe)
            manifest_path.write_text(
                json.dumps(MODULE.manifest_for(self.lock, "windows-amd64", binary)),
                encoding="utf-8",
            )
            with self.assertRaises(MODULE.PackagingError):
                MODULE.install_locked(
                    Namespace(
                        binary=str(binary),
                        destination=str(root / "output"),
                        lock=str(self.lock_path),
                        manifest=str(manifest_path),
                        manifest_destination=str(root / "output.json"),
                        platform="windows-386",
                    )
                )


if __name__ == "__main__":
    unittest.main()
