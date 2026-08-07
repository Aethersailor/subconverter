import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FetchPurposeBoundaryTests(unittest.TestCase):
    def test_fetch_arguments_default_to_generic(self):
        header = (ROOT / "src" / "handler" / "webget.h").read_text(
            encoding="utf-8"
        )
        self.assertIn("enum class FetchPurpose", header)
        self.assertIn("Generic,", header)
        self.assertIn("SubscriptionProvider", header)
        self.assertIn(
            "const FetchPurpose purpose = FetchPurpose::Generic;", header
        )
        self.assertRegex(
            header,
            r"FetchPurpose purpose = FetchPurpose::Generic\);",
        )

    def test_dispatcher_keeps_purposes_separate(self):
        source = (ROOT / "src" / "handler" / "webget.cpp").read_text(
            encoding="utf-8"
        )
        dispatcher = source.split("int FetchDispatcher::dispatch", 1)[1]
        self.assertRegex(
            dispatcher,
            r"case FetchPurpose::Generic:\s*return curlGet\(argument, result\);",
        )
        self.assertRegex(
            dispatcher,
            r"case FetchPurpose::SubscriptionProvider:[\s\S]*?"
            r"return mihomoFetch\(argument, result\);",
        )
        self.assertIn(
            'std::to_string(static_cast<int>(purpose)) + "\\n" + url + "\\n" + proxy',
            source,
        )
        self.assertIn("SUBCONVERTER_MIHOMO_COMMIT", source)

    def test_only_node_subscription_sources_use_subscription_purpose(self):
        nodemanip = (
            ROOT / "src" / "generator" / "config" / "nodemanip.cpp"
        ).read_text(encoding="utf-8")
        interfaces = (
            ROOT / "src" / "handler" / "interfaces.cpp"
        ).read_text(encoding="utf-8")

        self.assertRegex(
            nodemanip,
            r"strSub\s*=\s*webGet\(link, proxy, global\.cacheSubscription,"
            r"\s*&extra_headers, request_headers,\s*"
            r"FetchPurpose::SubscriptionProvider\);",
        )
        self.assertRegex(
            interfaces,
            r"base_content\s*=\s*fetchFile\(url, proxy, global\.cacheConfig,"
            r"\s*true, FetchPurpose::SubscriptionProvider\);",
        )
        self.assertRegex(
            interfaces,
            r"content\s*=\s*fetchFile\(url, proxy, global\.cacheSubscription,"
            r"\s*true,\s*FetchPurpose::SubscriptionProvider\);",
        )

        allowed = {
            ROOT / "src" / "handler" / "webget.cpp",
            ROOT / "src" / "generator" / "config" / "nodemanip.cpp",
            ROOT / "src" / "handler" / "interfaces.cpp",
        }
        unexpected = []
        for path in (ROOT / "src").rglob("*.cpp"):
            if path not in allowed and "FetchPurpose::SubscriptionProvider" in path.read_text(
                encoding="utf-8"
            ):
                unexpected.append(str(path.relative_to(ROOT)))
        self.assertEqual(unexpected, [])

    def test_async_fetch_forwards_purpose_to_web_get(self):
        source = (ROOT / "src" / "handler" / "multithread.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("[path, proxy, cache_ttl, purpose]", source)
        self.assertRegex(
            source,
            r"webGet\(path, proxy, cache_ttl, nullptr, nullptr, purpose\)",
        )


if __name__ == "__main__":
    unittest.main()
