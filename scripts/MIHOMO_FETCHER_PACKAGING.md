# Locked Mihomo fetcher packaging contract

Native release jobs do not build the companion helper locally. The official
Mihomo `toolchain.tar.gz` contains a Linux/amd64 Go toolchain, so one
Linux/amd64 preparation job must cross-compile the helper for every release
target first. The builder also verifies the matching locked official Mihomo
binary, extracts its embedded CA bundle, injects those exact bytes into the
helper build, and proves the resulting helper contains the same bundle.

For each target, run:

```sh
python3 scripts/package_mihomo_fetcher.py build \
  --platform linux-amd64 \
  --output artifacts/linux-amd64/subconverter-mihomo-fetcher \
  --manifest artifacts/linux-amd64/subconverter-mihomo-fetcher.manifest.json \
  --cache-dir .cache/mihomo-assets
```

Valid targets are `linux-386`, `linux-amd64`, `linux-armv7`, `linux-arm64`,
`macos-amd64`, `macos-arm64`, `windows-386`, and `windows-amd64`. Windows
outputs must use the `.exe` suffix. The build command downloads the exact
source-lock assets, verifies their sizes and SHA-256 identities, checks out the
exact Mihomo commit/tree, recovers the release's target-specific embedded CA
from the locked oracle artifact, and emits a content-addressed manifest.
The native Linux/amd64 build also runs the helper's Go tests with that locked
toolchain and vendor tree before compilation.

The native workflow then runs `scripts/test_mihomo_oracle_parity.py` against
the verified official Linux/amd64 release executable and the freshly built
helper. It compares the raw HTTP/1.1 request and parsed TLS ClientHello while
masking only audited per-connection randomness. HTTP/2 is reported separately
and is not inferred from source identity alone.

Before invoking any native release script, download the matching pair and set:

```sh
export SUBCONVERTER_MIHOMO_FETCHER_BIN=/path/to/subconverter-mihomo-fetcher
export SUBCONVERTER_MIHOMO_FETCHER_MANIFEST=/path/to/subconverter-mihomo-fetcher.manifest.json
```

Use `subconverter-mihomo-fetcher.exe` for Windows. `PYTHON_BIN` may select a
specific Python 3 executable. `SUBCONVERTER_MIHOMO_FETCHER_PLATFORM` is an
optional assertion; when present it must match the release host.

The release scripts install the verified binary beside `subconverter` and add
the manifest to the same package. Missing files, a stale source lock, the wrong
platform, or any identity mismatch aborts packaging. The Dockerfile performs
the same locked build directly in a Linux/amd64 build stage.
