import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReadmeContractTests(unittest.TestCase):
    def test_generated_source_is_declared_without_a_maintainer_section(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("自动生成", readme)
        self.assertIn("请修改模板，不要直接编辑 README.md", readme)
        self.assertNotIn("## README 如何维护", readme)

    def test_reader_journey_prioritizes_deployment_and_usage(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        introduction = readme[: readme.index("## 快速部署")]
        for value in (
            "最大限度减少原版 subconverter",
            "向订阅服务商暴露的请求特征",
            "尽可能对齐 Mihomo 内核的 Provider 访问特征",
            "订阅转换后端访问远程订阅服务商",
            "https://github.com/Aethersailor/SubConverter-Extended",
        ):
            self.assertIn(value, introduction)
        sections = (
            "## 快速部署",
            "## 开始使用",
            "## 这版解决什么问题",
            "## 更新",
            "## 常见问题",
            "<summary>开发与审计信息</summary>",
        )
        positions = [readme.index(section) for section in sections]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("/sub?target=<目标格式>", readme)
        self.assertIn("docker compose up -d", readme)
        self.assertIn("能直接替换原来的 subconverter 吗", readme)

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
