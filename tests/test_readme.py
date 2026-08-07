import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReadmeContractTests(unittest.TestCase):
    def test_generated_source_and_maintenance_command_are_declared(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("自动生成", readme)
        self.assertIn("请修改模板，不要直接编辑 README.md", readme)
        self.assertIn("python3 scripts/project_metadata.py render --check", readme)

    def test_local_links_exist(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", readme)
        local_links = []
        for link in links:
            target = link.split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            local_links.append(target)
            self.assertTrue((ROOT / target).exists(), target)
        self.assertIn("docs/PRIVACY.md", local_links)
        self.assertIn("mihomo-fetcher/PROTOCOL.md", local_links)
        self.assertIn("scripts/MIHOMO_FETCHER_PACKAGING.md", local_links)
        self.assertIn("scripts/mihomo_conformance/README.md", local_links)

    def test_documentation_workflow_covers_generated_sources(self):
        workflow = (ROOT / ".github" / "workflows" / "readme.yml").read_text(
            encoding="utf-8"
        )
        for value in (
            "README.md",
            ".github/templates/README.md.tmpl",
            ".github/project-metadata.json",
            ".github/source-lock.json",
            "docs/**",
            "python3 scripts/project_metadata.py render --check",
            "test_project_customization.py",
            "test_readme.py",
        ):
            self.assertIn(value, workflow)

    def test_heavy_workflows_ignore_documentation_only_changes(self):
        expected = (
            ".github/workflows/readme.yml",
            "docs/**",
            "mihomo-fetcher/PROTOCOL.md",
            "scripts/MIHOMO_FETCHER_PACKAGING.md",
            "scripts/mihomo_conformance/README.md",
            "tests/test_readme.py",
        )
        for relative in (
            ".github/workflows/build.yml",
            ".github/workflows/docker.yml",
        ):
            workflow = (ROOT / relative).read_text(encoding="utf-8")
            for value in expected:
                self.assertIn(value, workflow, "{}: {}".format(relative, value))


if __name__ == "__main__":
    unittest.main()
