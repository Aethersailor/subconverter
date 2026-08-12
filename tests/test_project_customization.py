import copy
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
            "{}-{}-{}.1a2b3c4d".format(
                metadata["upstream_version"],
                metadata["upstream_commit"][:8],
                metadata["edition"],
            ),
        )

    def test_readme_matches_metadata_template(self):
        metadata = PROJECT_METADATA.load_metadata()
        source_lock = PROJECT_METADATA.load_source_lock()
        expected = PROJECT_METADATA.render(metadata, source_lock)
        actual = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_mihomo_readme_version_comes_only_from_source_lock(self):
        metadata = PROJECT_METADATA.load_metadata()
        source_lock = PROJECT_METADATA.load_source_lock()
        self.assertNotIn("user_agent", metadata)
        self.assertNotIn("user_agent_source", metadata)
        self.assertNotIn("user_agent_updated_at", metadata)

        readme = PROJECT_METADATA.render(metadata, source_lock)
        mihomo = source_lock["mihomo"]
        self.assertIn(mihomo["tag"], readme)
        self.assertNotIn(mihomo["tag_identity"]["commit"], readme)
        self.assertNotIn(source_lock["pair_id"], readme)
        self.assertIn("显式标记为 `SubscriptionProvider`", readme)
        self.assertIn("不是只替换一个 User-Agent 字符串", readme)
        self.assertIn("Generic 出站请求", readme)

    def test_readme_render_fails_when_source_lock_and_metadata_diverge(self):
        metadata = PROJECT_METADATA.load_metadata()
        source_lock = copy.deepcopy(PROJECT_METADATA.load_source_lock())
        source_lock["subconverter"]["commit"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "source lock does not match"):
            PROJECT_METADATA.render(metadata, source_lock)

    def test_outbound_identity_has_no_legacy_fingerprint_headers(self):
        source = (ROOT / "src" / "handler" / "webget.cpp").read_text(encoding="utf-8")
        self.assertNotIn("SubConverter-Request:", source)
        self.assertNotIn("SubConverter-Version:", source)
        self.assertNotIn("X-Requested-With: subconverter", source)
        self.assertIn('"subconverter/" VERSION " cURL/" LIBCURL_VERSION', source)

    def test_subscription_entry_headers_are_sanitized(self):
        source = (ROOT / "src" / "handler" / "interfaces.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("sanitizeSubscriptionRequestHeaders", source)
        self.assertIn("(void)headers;", source)
        self.assertNotIn("string_icase_map sanitized = headers", source)

        integration_test = (
            ROOT / "scripts" / "test_outbound_headers.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'allowed = {"accept", "accept-encoding", "host", "user-agent"}',
            integration_test,
        )

    def test_inbound_verbose_logs_do_not_emit_query_tokens_or_header_values(self):
        source = (ROOT / "src" / "server" / "webserver_httplib.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("req.target.find('?')", source)
        self.assertIn('"handle_header_names: " + header_names', source)
        self.assertNotIn('" handle_uri:    " + req.target', source)
        self.assertNotIn('"handle_header: " + dump(req.headers)', source)

    def test_metadata_uses_full_upstream_commit(self):
        metadata = PROJECT_METADATA.load_metadata()
        self.assertRegex(metadata["upstream_commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(metadata["upstream_version"], r"^v[0-9]+\.[0-9]+\.[0-9]+$")

    def test_sync_report_supports_repositories_with_issues_disabled(self):
        workflow = (ROOT / ".github" / "workflows" / "auto_sync.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("GH_REPO: ${{ github.repository }}", workflow)
        self.assertIn('"repos/$GITHUB_REPOSITORY"', workflow)
        self.assertIn("python3 scripts/retry_gh_api.py --", workflow)
        self.assertIn('if [[ "$repo_has_issues" != "true" ]]', workflow)
        self.assertNotIn(
            "needs.publish.result == 'success' &&\n"
            "      (needs.release.result == 'success'",
            workflow,
        )

    def test_sync_preserves_cross_platform_helper_line_endings(self):
        workflow = (ROOT / ".github" / "workflows" / "auto_sync.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("            .gitattributes\n", workflow)

    def test_github_api_reads_use_the_shared_retry_boundary(self):
        expected_uses = {
            "auto_sync.yml": 2,
            "build.yml": 1,
            "docker.yml": 1,
        }
        for name, expected_count in expected_uses.items():
            with self.subTest(workflow=name):
                workflow = (
                    ROOT / ".github" / "workflows" / name
                ).read_text(encoding="utf-8")
                self.assertEqual(
                    workflow.count("python3 scripts/retry_gh_api.py --"),
                    expected_count,
                )
                self.assertNotIn("gh api ", workflow)

        sync_workflow = (
            ROOT / ".github" / "workflows" / "auto_sync.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("- name: Checkout workflow utilities", sync_workflow)
        self.assertIn("            scripts/retry_gh_api.py\n", sync_workflow)
        self.assertIn("            tests/test_retry_gh_api.py\n", sync_workflow)

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
            "needs.build.result == 'success'",
            "Manifest publication did not succeed",
        ):
            self.assertIn(value, workflow)


if __name__ == "__main__":
    unittest.main()
