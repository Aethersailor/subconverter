# SubConverter Mihomo fetcher protocol

The helper is built as an overlay inside the exact locked Mihomo source tree.
It communicates only through stdin/stdout. It never opens a listening socket.

Each message is CBOR preceded by a four-byte unsigned big-endian length. The
protocol version is reported by the first `hello` frame. Request and response
frames carry an unsigned `id`; responses may arrive out of order.

The protocol supports DIRECT plus standard HTTP, HTTPS, SOCKS5, and SOCKS5H
proxy URLs. Windows per-protocol Internet Settings values are selected by the
subscription URL scheme before being mapped to the same adapters. Every
profile uses Mihomo's `HTTPVehicle`, inner tunnel, official
outbound adapter, proxy dialer, provider timeout, ETag state, raw response
bytes, repeated response headers, and certificate validation. Unsupported
proxy forms fail closed instead of falling back to cURL or silently going
DIRECT.

stdout is reserved for framed CBOR. Mihomo package logging is suppressed before
resource or cache initialization so an unstructured log line cannot corrupt
the transport.

Subscription URLs, credentials, headers, and bodies must not be placed in
command-line arguments, environment variables, logs, crash messages, or CI
summaries. A helper identity of `unknown` is a development build and must be
rejected by release-mode callers.
