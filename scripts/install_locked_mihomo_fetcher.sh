#!/bin/bash

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
destination_directory="${1:-$repository_root/base}"

if [[ -n "${SUBCONVERTER_MIHOMO_FETCHER_PLATFORM:-}" ]]; then
    helper_platform="$SUBCONVERTER_MIHOMO_FETCHER_PLATFORM"
else
    case "$(uname -s):$(uname -m)" in
        Linux:x86_64|Linux:amd64) helper_platform=linux-amd64 ;;
        Linux:i386|Linux:i486|Linux:i586|Linux:i686) helper_platform=linux-386 ;;
        Linux:armv7|Linux:armv7l) helper_platform=linux-armv7 ;;
        Linux:aarch64|Linux:arm64) helper_platform=linux-arm64 ;;
        Darwin:x86_64|Darwin:amd64) helper_platform=macos-amd64 ;;
        Darwin:arm64|Darwin:aarch64) helper_platform=macos-arm64 ;;
        MINGW*:i386|MINGW*:i486|MINGW*:i586|MINGW*:i686|MSYS*:i386|MSYS*:i486|MSYS*:i586|MSYS*:i686) helper_platform=windows-386 ;;
        MINGW*:x86_64|MINGW*:amd64|MSYS*:x86_64|MSYS*:amd64) helper_platform=windows-amd64 ;;
        *)
            echo "unsupported Mihomo helper packaging host: $(uname -s) $(uname -m)" >&2
            exit 1
            ;;
    esac
fi

case "$helper_platform" in
    linux-386|linux-amd64|linux-armv7|linux-arm64|macos-amd64|macos-arm64)
        helper_filename=subconverter-mihomo-fetcher
        ;;
    windows-386|windows-amd64)
        helper_filename=subconverter-mihomo-fetcher.exe
        ;;
    *)
        echo "unsupported Mihomo helper platform: $helper_platform" >&2
        exit 1
        ;;
esac

: "${SUBCONVERTER_MIHOMO_FETCHER_BIN:?workflow must provide SUBCONVERTER_MIHOMO_FETCHER_BIN}"
: "${SUBCONVERTER_MIHOMO_FETCHER_MANIFEST:?workflow must provide SUBCONVERTER_MIHOMO_FETCHER_MANIFEST}"

python_command="${PYTHON_BIN:-}"
if [[ -z "$python_command" ]]; then
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            python_command="$candidate"
            break
        fi
    done
fi
if [[ -z "$python_command" ]]; then
    echo "Python 3 is required to verify the locked Mihomo helper" >&2
    exit 1
fi

mkdir -p "$destination_directory"
"$python_command" "$repository_root/scripts/package_mihomo_fetcher.py" install \
    --platform "$helper_platform" \
    --binary "$SUBCONVERTER_MIHOMO_FETCHER_BIN" \
    --manifest "$SUBCONVERTER_MIHOMO_FETCHER_MANIFEST" \
    --destination "$destination_directory/$helper_filename" \
    --manifest-destination "$destination_directory/subconverter-mihomo-fetcher.manifest.json"

test -s "$destination_directory/$helper_filename"
test -s "$destination_directory/subconverter-mihomo-fetcher.manifest.json"
