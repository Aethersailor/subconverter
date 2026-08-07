# Mihomo conformance primitives

This directory contains the offline, fail-closed contract layer for comparing
an official Mihomo capture with a SubConverter capture. It does **not** fetch a
release, start either program, or claim to capture TLS or HTTP/2 traffic.

The v1 contract consists of:

- `capture-v1.schema.json`: a closed capture schema. Unknown fields, missing
  fields, collector errors, and incomplete captures are invalid.
- `normalization-policy-v1.json`: the complete audited list of nondeterministic
  values that may be masked, plus the one numeric timing tolerance.
- `identity.py`: validation of already-fetched GitHub Release metadata and the
  exact downloaded asset bytes. It performs no network access.
- `normalization.py` and `diff.py`: deterministic normalization and structured
  ordered comparison.

Capture profiles make applicability explicit:

- `http1_plaintext` requires DNS, TCP, HTTP/1.1, and application observations.
- `https_http1` additionally requires TLS observations.
- `https_http2` requires TLS and HTTP/2 observations, including SETTINGS and
  ordered pseudo-headers.

There is intentionally no `not_captured` layer state. A collector must either
provide all fields required by the selected profile with `capture_complete`
set to `true` and no collector errors, or validation fails. `not_applicable` is
accepted only where the static profile policy requires it.

Run the offline tests from the repository root:

```shell
python -m unittest discover -s tests -p 'test_mihomo_conformance.py' -v
```
