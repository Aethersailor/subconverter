import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "cleanup_container_registry.py"
SPEC = importlib.util.spec_from_file_location("cleanup_container_registry", MODULE_PATH)
CLEANUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLEANUP)


def ghcr_version(version_id, name, tags=()):
    return {
        "id": version_id,
        "name": name,
        "metadata": {"container": {"tags": list(tags)}},
    }


class ContainerRegistryCleanupTests(unittest.TestCase):
    def test_dockerhub_cleanup_only_deletes_non_latest_tags(self):
        tags = [{"name": "sha-deadbeef"}, {"name": "latest"}, {"name": "edge"}]
        self.assertEqual(
            CLEANUP.dockerhub_tags_to_delete(tags),
            ["edge", "sha-deadbeef"],
        )

    def test_dockerhub_cleanup_refuses_to_run_without_latest(self):
        with self.assertRaisesRegex(CLEANUP.RegistryCleanupError, "latest.*missing"):
            CLEANUP.dockerhub_tags_to_delete([{"name": "edge"}])

    def test_manifest_keep_set_includes_index_and_all_children(self):
        manifest = {
            "manifests": [
                {"digest": "sha256:child1"},
                {"digest": "sha256:child2"},
            ]
        }
        self.assertEqual(
            CLEANUP.manifest_keep_digests("sha256:index", manifest),
            {"sha256:index", "sha256:child1", "sha256:child2"},
        )

    def test_ghcr_cleanup_preserves_latest_manifest_closure(self):
        keep = {"sha256:index", "sha256:amd64", "sha256:arm64"}
        versions = [
            ghcr_version(1, "sha256:index", ["latest"]),
            ghcr_version(2, "sha256:amd64"),
            ghcr_version(3, "sha256:arm64"),
            ghcr_version(4, "sha256:old-index", ["sha-deadbeef", "edge"]),
            ghcr_version(5, "sha256:old-child"),
        ]
        obsolete = CLEANUP.select_ghcr_versions_for_deletion(versions, keep)
        self.assertEqual([item["id"] for item in obsolete], [4, 5])

    def test_ghcr_cleanup_refuses_latest_with_obsolete_aliases(self):
        versions = [
            ghcr_version(1, "sha256:index", ["latest", "sha-deadbeef"]),
            ghcr_version(2, "sha256:amd64"),
        ]
        with self.assertRaisesRegex(
            CLEANUP.RegistryCleanupError, "obsolete aliases"
        ):
            CLEANUP.select_ghcr_versions_for_deletion(
                versions, {"sha256:index", "sha256:amd64"}
            )

    def test_ghcr_cleanup_requires_exactly_one_latest(self):
        with self.assertRaisesRegex(CLEANUP.RegistryCleanupError, "exactly one"):
            CLEANUP.select_ghcr_versions_for_deletion(
                [ghcr_version(1, "sha256:child")], {"sha256:child"}
            )


if __name__ == "__main__":
    unittest.main()
