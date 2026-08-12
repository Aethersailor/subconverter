import importlib.util
import io
import subprocess
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "retry_gh_api.py"
SPEC = importlib.util.spec_from_file_location("retry_gh_api", MODULE_PATH)
RETRY_GH_API = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RETRY_GH_API)


def result(returncode, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(
        args=["gh", "api"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class RetryGitHubApiTests(unittest.TestCase):
    def test_retries_with_exponential_backoff_and_returns_only_success(self):
        runner = mock.Mock(
            side_effect=[
                result(1, stdout=b'{"partial":', stderr=b"temporary TLS failure"),
                result(1, stdout=b"gateway error", stderr=b"HTTP 502"),
                result(0, stdout=b'{"ok":true}\n'),
            ]
        )
        delays = []
        errors = io.StringIO()

        payload = RETRY_GH_API.request_with_retry(
            ["repos/example/project"],
            attempts=4,
            base_delay=2,
            runner=runner,
            sleeper=delays.append,
            error_stream=errors,
        )

        self.assertEqual(payload, b'{"ok":true}\n')
        self.assertEqual(delays, [2, 4])
        self.assertEqual(runner.call_count, 3)
        self.assertNotIn("partial", errors.getvalue())
        self.assertIn("temporary TLS failure", errors.getvalue())
        self.assertIn("HTTP 502", errors.getvalue())

    def test_reports_the_last_error_after_the_retry_budget_is_exhausted(self):
        runner = mock.Mock(
            side_effect=[
                result(1, stderr=b"temporary TLS failure"),
                result(1, stderr=b"certificate still invalid"),
            ]
        )
        delays = []

        with self.assertRaisesRegex(
            RETRY_GH_API.GitHubApiError,
            "failed after 2 attempts: certificate still invalid",
        ):
            RETRY_GH_API.request_with_retry(
                ["repos/example/project"],
                attempts=2,
                base_delay=1,
                runner=runner,
                sleeper=delays.append,
                error_stream=io.StringIO(),
            )

        self.assertEqual(delays, [1])

    def test_missing_gh_fails_immediately(self):
        runner = mock.Mock(side_effect=FileNotFoundError("gh not found"))
        sleeper = mock.Mock()

        with self.assertRaisesRegex(RETRY_GH_API.GitHubApiError, "unable to execute gh"):
            RETRY_GH_API.request_with_retry(
                ["repos/example/project"], runner=runner, sleeper=sleeper
            )

        self.assertEqual(runner.call_count, 1)
        sleeper.assert_not_called()

    def test_rejects_invalid_retry_settings(self):
        with self.assertRaisesRegex(ValueError, "attempts"):
            RETRY_GH_API.request_with_retry(["repos/example/project"], attempts=0)
        with self.assertRaisesRegex(ValueError, "base_delay"):
            RETRY_GH_API.request_with_retry(
                ["repos/example/project"], base_delay=-1
            )


if __name__ == "__main__":
    unittest.main()
