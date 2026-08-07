import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_mihomo_fetcher.py"
SPEC = importlib.util.spec_from_file_location("build_mihomo_fetcher", MODULE_PATH)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class MihomoFetcherBuildTests(unittest.TestCase):
    def test_helper_overlay_line_endings_are_pinned_across_platforms(self):
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.go text eol=lf", attributes.splitlines())

    def test_overlay_digest_matches_source_lock(self):
        lock, version, commit, digest = BUILDER.load_lock()
        self.assertEqual(version, lock["mihomo"]["tag"])
        self.assertEqual(commit, lock["mihomo"]["tag_identity"]["commit"])
        self.assertEqual(digest, lock["project"]["helper_overlay_sha256"])

    def test_all_published_platforms_have_build_targets_and_oracles(self):
        lock, _, _, _ = BUILDER.load_lock()
        self.assertEqual(
            set(BUILDER.PLATFORM_TARGETS), set(lock["mihomo"]["oracle_assets"])
        )

    def test_native_locked_build_runs_helper_tests_before_compilation(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        test_position = source.index('"test",')
        build_position = source.index('"build",', test_position)
        self.assertLess(test_position, build_position)
        self.assertIn('args.platform == "linux-amd64"', source)
        self.assertIn("TEST_OVERLAY_FILES", source)

    def test_changed_overlay_fails_closed(self):
        lock = json.loads((ROOT / ".github" / "source-lock.json").read_text())
        lock["project"]["helper_overlay_sha256"] = "sha256:" + "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock.json"
            path.write_text(json.dumps(lock), encoding="utf-8")
            with self.assertRaisesRegex(BUILDER.BuildError, "does not match"):
                BUILDER.load_lock(path)

    def test_source_export_rejects_any_dirty_or_untracked_input(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            subprocess.run(["git", "init", "--quiet", source], check=True)
            subprocess.run(
                ["git", "-C", source, "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", source, "config", "user.name", "Source Lock Test"],
                check=True,
            )
            (source / "tracked.go").write_text("package locked\n", encoding="utf-8")
            subprocess.run(["git", "-C", source, "add", "tracked.go"], check=True)
            subprocess.run(
                ["git", "-C", source, "commit", "--quiet", "-m", "locked"],
                check=True,
            )
            commit, tree = BUILDER.git_identity(source)
            BUILDER.require_clean_source(source, commit, tree)

            (source / "untracked.go").write_text("package injected\n", encoding="utf-8")
            with self.assertRaisesRegex(BUILDER.BuildError, "completely clean"):
                BUILDER.require_clean_source(source, commit, tree)

    def test_source_export_comes_from_git_object_and_injects_only_locked_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "--quiet", source], check=True)
            subprocess.run(
                ["git", "-C", source, "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", source, "config", "user.name", "Source Lock Test"],
                check=True,
            )
            (source / "component" / "ca").mkdir(parents=True)
            (source / "component" / "ca" / "ca-certificates.crt").write_bytes(b"")
            (source / "cmd").mkdir()
            (source / "tracked.go").write_text("package locked\n", encoding="utf-8")
            subprocess.run(["git", "-C", source, "add", "."], check=True)
            subprocess.run(
                ["git", "-C", source, "commit", "--quiet", "-m", "locked"],
                check=True,
            )
            ca_bundle = root / "ca.crt"
            ca_bundle.write_bytes(b"locked ca\n")

            original_overlay = BUILDER.OVERLAY_FILES
            try:
                BUILDER.OVERLAY_FILES = ()
                destination = root / "export"
                BUILDER.copy_source(source, destination, ca_bundle)
            finally:
                BUILDER.OVERLAY_FILES = original_overlay

            self.assertEqual(
                (destination / "component" / "ca" / "ca-certificates.crt").read_bytes(),
                b"locked ca\n",
            )
            self.assertEqual(
                (destination / "tracked.go").read_text(encoding="utf-8"),
                "package locked\n",
            )
            self.assertFalse((destination / ".git").exists())


if __name__ == "__main__":
    unittest.main()
