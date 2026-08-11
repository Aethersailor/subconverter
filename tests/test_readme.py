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

    def test_only_main_title_is_bilingual(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        introduction = readme[: readme.index("## 🚀 部署方法")]
        for value in (
            "最大限度减少原版 subconverter",
            "向订阅服务商暴露的请求特征",
            "尽可能对齐 Mihomo 内核的 Provider 访问特征",
            "订阅转换后端访问远程订阅服务商",
            "https://github.com/Aethersailor/SubConverter-Extended",
        ):
            self.assertIn(value, introduction)

        sections = (
            "## 🎯 项目定位",
            "## 🔍 与上游的详细区别",
            "## 🚀 部署方法",
            "## ⚙️ 仓库运行逻辑",
            "## 🛡️ 能力边界",
            "## 🔄 更新",
            "## ❓ 常见问题",
            "🔎 可验证身份",
        )
        positions = [readme.index(section) for section in sections]
        self.assertEqual(positions, sorted(positions))

        self.assertIn(
            '<h1 align="center">subconverter 隐匿特征版 / Anti-Fingerprint Edition</h1>',
            readme,
        )
        self.assertNotRegex(readme, r"(?m)^#{2,6} .+ / .+$")
        self.assertNotRegex(readme, r"<summary>[^\n]* / [^\n]*</summary>")
        self.assertNotRegex(readme, r"\[[^\]]+ / [^\]]+\]\(")
        self.assertNotIn("项目定位 / Positioning", readme)
        self.assertNotIn("完整 Wiki / Full Wiki", readme)
        self.assertIn("docker compose up -d", readme)
        self.assertIn("```mermaid", readme)
        self.assertIn("https://github.com/Aethersailor/subconverter/wiki", readme)

    def test_generic_upstream_usage_is_not_duplicated(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for value in (
            "常用目标格式",
            "--data-urlencode",
            "/sub?target=<目标格式>",
        ):
            self.assertNotIn(value, readme)
        self.assertIn("上游 README", readme)

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

    def test_published_outputs_are_documented_as_docker_only(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for value in (
            "img.shields.io/github/v/release/Aethersailor/subconverter",
            "github.com/Aethersailor/subconverter/releases",
            "原生程序：[GitHub Releases]",
        ):
            self.assertNotIn(value, readme)
        self.assertIn("## 📦 镜像与平台", readme)
        self.assertIn("docker.io/aethersailor/subconverter:latest", readme)
        self.assertIn("ghcr.io/aethersailor/subconverter:latest", readme)

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
