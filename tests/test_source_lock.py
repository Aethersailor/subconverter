import base64
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "resolve_source_lock.py"
SPEC = importlib.util.spec_from_file_location("resolve_source_lock", MODULE_PATH)
SOURCE_LOCK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCE_LOCK)


class MappingClient:
    def __init__(self, responses):
        self.responses = responses

    def get_json(self, path):
        if path not in self.responses:
            raise AssertionError("unexpected fixture request: {}".format(path))
        return copy.deepcopy(self.responses[path])


def fake_sha(character):
    return character * 40


def fake_digest(index):
    return "sha256:" + format(index, "x")[-1] * 64


def verification():
    return {
        "reason": "valid",
        "verified": True,
        "verified_at": "2026-01-01T00:00:00Z",
    }


def fixture_responses(tag="v1.19.29", annotated=False):
    upstream_commit = fake_sha("1")
    upstream_tree = fake_sha("2")
    mihomo_commit = fake_sha("3")
    mihomo_tree = fake_sha("4")
    tag_object = fake_sha("5")
    version_blob = fake_sha("6")

    responses = {
        "/repos/asdlokj1qpi233/subconverter/git/ref/heads/master": {
            "object": {"sha": upstream_commit, "type": "commit"}
        },
        "/repos/asdlokj1qpi233/subconverter/git/commits/{}".format(upstream_commit): {
            "sha": upstream_commit,
            "tree": {"sha": upstream_tree},
            "verification": verification(),
        },
        "/repos/asdlokj1qpi233/subconverter/contents/src/version.h?ref={}".format(
            upstream_commit
        ): {
            "content": base64.b64encode(b'#define VERSION "v0.9.9"\n').decode("ascii"),
            "encoding": "base64",
            "sha": version_blob,
            "type": "file",
        },
        "/repos/MetaCubeX/mihomo/git/commits/{}".format(mihomo_commit): {
            "sha": mihomo_commit,
            "tree": {"sha": mihomo_tree},
            "verification": verification(),
        },
    }

    if annotated:
        responses["/repos/MetaCubeX/mihomo/git/ref/tags/{}".format(tag)] = {
            "object": {"sha": tag_object, "type": "tag"}
        }
        responses["/repos/MetaCubeX/mihomo/git/tags/{}".format(tag_object)] = {
            "object": {"sha": mihomo_commit, "type": "commit"},
            "sha": tag_object,
            "tag": tag,
            "verification": verification(),
        }
    else:
        responses["/repos/MetaCubeX/mihomo/git/ref/tags/{}".format(tag)] = {
            "object": {"sha": mihomo_commit, "type": "commit"}
        }

    asset_names = list(SOURCE_LOCK.REQUIRED_RELEASE_ASSETS.values())
    asset_names.extend(
        template.format(tag=tag)
        for template in SOURCE_LOCK.ORACLE_ASSET_TEMPLATES.values()
    )
    assets = []
    for index, name in enumerate(asset_names, start=1):
        assets.append(
            {
                "browser_download_url": "https://example.invalid/{}/{}".format(tag, name),
                "digest": fake_digest(index),
                "id": 1000 + index,
                "name": name,
                "size": 100000 + index,
                "state": "uploaded",
            }
        )
    responses["/repos/MetaCubeX/mihomo/releases/latest"] = {
        "assets": assets,
        "created_at": "2026-01-01T00:00:00Z",
        "draft": False,
        "html_url": "https://github.com/MetaCubeX/mihomo/releases/tag/{}".format(tag),
        "id": 900,
        "immutable": False,
        "prerelease": False,
        "published_at": "2026-01-01T01:00:00Z",
        "tag_name": tag,
    }
    return responses


class SourceLockTests(unittest.TestCase):
    def test_resolves_lightweight_tag_and_all_published_platforms(self):
        lock = SOURCE_LOCK.resolve_source_lock(MappingClient(fixture_responses()))

        self.assertEqual(lock["schema_version"], 1)
        self.assertRegex(lock["pair_id"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(lock["subconverter"]["commit"], fake_sha("1"))
        self.assertEqual(lock["subconverter"]["tree"], fake_sha("2"))
        self.assertEqual(lock["subconverter"]["version"], "v0.9.9")
        self.assertEqual(lock["mihomo"]["tag"], "v1.19.29")
        self.assertEqual(lock["mihomo"]["tag_identity"]["commit"], fake_sha("3"))
        self.assertEqual(lock["mihomo"]["tag_identity"]["tree"], fake_sha("4"))
        self.assertEqual(lock["mihomo"]["tag_identity"]["ref_object_type"], "commit")
        self.assertEqual(lock["mihomo"]["tag_identity"]["annotated_tags"], [])
        self.assertEqual(lock["project"]["helper_protocol"], 1)
        self.assertEqual(
            lock["project"]["parity_contract"], "mihomo-provider-fetch-v1"
        )
        self.assertRegex(
            lock["project"]["helper_overlay_sha256"], r"^sha256:[0-9a-f]{64}$"
        )
        self.assertEqual(
            set(lock["mihomo"]["oracle_assets"]),
            set(SOURCE_LOCK.ORACLE_ASSET_TEMPLATES),
        )
        self.assertEqual(
            set(lock["mihomo"]["required_assets"]),
            set(SOURCE_LOCK.REQUIRED_RELEASE_ASSETS),
        )

    def test_peels_annotated_tag_to_commit_and_tree(self):
        lock = SOURCE_LOCK.resolve_source_lock(
            MappingClient(fixture_responses(tag="v1.19.30", annotated=True))
        )

        identity = lock["mihomo"]["tag_identity"]
        self.assertEqual(identity["ref_object_type"], "tag")
        self.assertEqual(identity["ref_object_sha"], fake_sha("5"))
        self.assertEqual(identity["commit"], fake_sha("3"))
        self.assertEqual(identity["tree"], fake_sha("4"))
        self.assertEqual(len(identity["annotated_tags"]), 1)
        self.assertEqual(identity["annotated_tags"][0]["name"], "v1.19.30")
        self.assertEqual(identity["annotated_tags"][0]["target_type"], "commit")

    def test_rejects_draft_or_prerelease_as_latest_stable(self):
        for field in ("draft", "prerelease"):
            with self.subTest(field=field):
                responses = fixture_responses()
                responses["/repos/MetaCubeX/mihomo/releases/latest"][field] = True
                with self.assertRaisesRegex(
                    SOURCE_LOCK.SourceLockError, "must be stable"
                ):
                    SOURCE_LOCK.resolve_source_lock(MappingClient(responses))

    def test_rejects_release_downgrade(self):
        newer = SOURCE_LOCK.resolve_source_lock(
            MappingClient(fixture_responses(tag="v1.19.30"))
        )
        with self.assertRaisesRegex(SOURCE_LOCK.SourceLockError, "downgrade rejected"):
            SOURCE_LOCK.resolve_source_lock(
                MappingClient(fixture_responses(tag="v1.19.29")), previous=newer
            )

    def test_rejects_same_tag_asset_mutation(self):
        original_responses = fixture_responses()
        previous = SOURCE_LOCK.resolve_source_lock(MappingClient(original_responses))
        changed_responses = copy.deepcopy(original_responses)
        assets = changed_responses["/repos/MetaCubeX/mihomo/releases/latest"]["assets"]
        assets[0]["digest"] = "sha256:" + "f" * 64

        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError, "changed without a version change"
        ):
            SOURCE_LOCK.resolve_source_lock(
                MappingClient(changed_responses), previous=previous
            )

    def test_rejects_missing_github_asset_digest(self):
        responses = fixture_responses()
        assets = responses["/repos/MetaCubeX/mihomo/releases/latest"]["assets"]
        assets[0]["digest"] = None

        with self.assertRaisesRegex(SOURCE_LOCK.SourceLockError, "digest"):
            SOURCE_LOCK.resolve_source_lock(MappingClient(responses))

    def test_offline_fixture_cli_writes_and_checks_lock(self):
        responses = fixture_responses(annotated=True)
        with tempfile.TemporaryDirectory() as directory:
            fixture_dir = Path(directory) / "fixture"
            fixture_dir.mkdir()
            (fixture_dir / "responses.json").write_text(
                json.dumps(responses), encoding="utf-8"
            )
            output = Path(directory) / "source-lock.json"

            result = SOURCE_LOCK.main(
                ["--fixture-dir", str(fixture_dir), "--output", str(output)]
            )
            self.assertEqual(result, 0)
            self.assertTrue(output.exists())
            result = SOURCE_LOCK.main(
                [
                    "--fixture-dir",
                    str(fixture_dir),
                    "--output",
                    str(output),
                    "--check",
                ]
            )
            self.assertEqual(result, 0)

    def test_same_mihomo_identity_allows_new_subconverter_commit(self):
        responses = fixture_responses()
        previous = SOURCE_LOCK.resolve_source_lock(MappingClient(responses))
        changed = copy.deepcopy(responses)
        new_commit = fake_sha("7")
        changed["/repos/asdlokj1qpi233/subconverter/git/ref/heads/master"]["object"][
            "sha"
        ] = new_commit
        changed[
            "/repos/asdlokj1qpi233/subconverter/git/commits/{}".format(new_commit)
        ] = {
            "sha": new_commit,
            "tree": {"sha": fake_sha("8")},
            "verification": verification(),
        }
        changed[
            "/repos/asdlokj1qpi233/subconverter/contents/src/version.h?ref={}".format(
                new_commit
            )
        ] = copy.deepcopy(
            changed[
                "/repos/asdlokj1qpi233/subconverter/contents/src/version.h?ref={}".format(
                    fake_sha("1")
                )
            ]
        )

        current = SOURCE_LOCK.resolve_source_lock(MappingClient(changed), previous=previous)
        self.assertEqual(current["subconverter"]["commit"], new_commit)
        self.assertNotEqual(current["pair_id"], previous["pair_id"])


if __name__ == "__main__":
    unittest.main()
