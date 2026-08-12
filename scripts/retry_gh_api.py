#!/usr/bin/env python3

"""Run one GitHub CLI API request with bounded exponential retries."""

import argparse
import subprocess
import sys
import time


DEFAULT_ATTEMPTS = 4
DEFAULT_BASE_DELAY = 2.0


class GitHubApiError(RuntimeError):
    """Raised when a GitHub API request cannot be completed safely."""


def _error_text(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value).strip()


def request_with_retry(
    gh_args,
    attempts=DEFAULT_ATTEMPTS,
    base_delay=DEFAULT_BASE_DELAY,
    runner=subprocess.run,
    sleeper=time.sleep,
    error_stream=sys.stderr,
):
    """Return stdout from ``gh api`` without exposing partial failed responses."""

    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if base_delay < 0:
        raise ValueError("base_delay must not be negative")
    if not gh_args:
        raise ValueError("at least one gh api argument is required")

    command = ["gh", "api"] + list(gh_args)
    for attempt in range(1, attempts + 1):
        try:
            result = runner(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            raise GitHubApiError("unable to execute gh: {}".format(error))

        if result.returncode == 0:
            return result.stdout

        detail = _error_text(result.stderr) or "exit code {}".format(result.returncode)
        if attempt == attempts:
            raise GitHubApiError(
                "GitHub API request failed after {} attempts: {}".format(
                    attempts, detail
                )
            )

        delay = base_delay * (2 ** (attempt - 1))
        print(
            "GitHub API request attempt {}/{} failed: {}; retrying in {:g}s".format(
                attempt, attempts, detail, delay
            ),
            file=error_stream,
        )
        sleeper(delay)

    raise AssertionError("retry loop exited unexpectedly")


def parse_arguments(argv):
    if "--" not in argv:
        raise GitHubApiError("separate wrapper options from gh api arguments with --")

    separator = argv.index("--")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    parser.add_argument("--base-delay", type=float, default=DEFAULT_BASE_DELAY)
    options = parser.parse_args(argv[:separator])
    gh_args = argv[separator + 1 :]
    return options, gh_args


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        options, gh_args = parse_arguments(argv)
        payload = request_with_retry(
            gh_args,
            attempts=options.attempts,
            base_delay=options.base_delay,
        )
    except (GitHubApiError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
