import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "project_metadata.py"
SPEC = importlib.util.spec_from_file_location("project_metadata", MODULE_PATH)
PROJECT_METADATA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROJECT_METADATA)


class ProjectCustomizationTests(unittest.TestCase):
    def test_version_preserves_upstream_format_and_appends_project_suffix(self):
        metadata = PROJECT_METADATA.load_metadata()
        self.assertEqual(
            PROJECT_METADATA.build_version(metadata, "1a2b3c4d99887766"),
            "v0.9.9-633ecd5a-af.1a2b3c4d",
        )

    def test_readme_matches_metadata_template(self):
        metadata = PROJECT_METADATA.load_metadata()
        expected = PROJECT_METADATA.render(metadata)
        actual = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_outbound_identity_has_no_legacy_fingerprint_headers(self):
        source = (ROOT / "src" / "handler" / "webget.cpp").read_text(encoding="utf-8")
        self.assertNotIn("SubConverter-Request:", source)
        self.assertNotIn("SubConverter-Version:", source)
        self.assertNotIn("X-Requested-With: subconverter", source)
        self.assertIn("SUBCONVERTER_OUTBOUND_USER_AGENT", source)

    def test_subscription_entry_headers_are_sanitized(self):
        source = (ROOT / "src" / "handler" / "interfaces.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("sanitizeSubscriptionRequestHeaders", source)
        for header in (
            "Host",
            "User-Agent",
            "Content-Type",
            "X-Forwarded-For",
            "SubConverter-Request",
            "SubConverter-Version",
        ):
            self.assertIn('sanitized.erase("{}")'.format(header), source)

    def test_metadata_uses_full_upstream_commit(self):
        metadata = PROJECT_METADATA.load_metadata()
        self.assertRegex(metadata["upstream_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(metadata["upstream_version"], "v0.9.9")

    def test_windows_build_patches_pinned_yaml_cpp_for_current_compilers(self):
        script = (ROOT / "scripts" / "build.windows.release.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("sed -i '1i#include <cstdint>' src/emitterutils.cpp", script)
        self.assertEqual(script.count("-DCMAKE_POLICY_VERSION_MINIMUM=3.5"), 2)
        self.assertIn(
            "0002-rapidjson-disable-string-ref-assignment.patch", script
        )

    def test_docker_registry_digests_are_kept_separate(self):
        workflow = (ROOT / ".github" / "workflows" / "docker.yml").read_text(
            encoding="utf-8"
        )
        for value in (
            "steps.dockerhub_image.outputs.digest",
            "steps.ghcr_image.outputs.digest",
            "image-digest-dockerhub-*",
            "image-digest-ghcr-*",
        ):
            self.assertIn(value, workflow)


if __name__ == "__main__":
    unittest.main()
