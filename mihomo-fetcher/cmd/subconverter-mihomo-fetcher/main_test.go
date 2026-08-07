package main

import (
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	mihomoHTTP "github.com/metacubex/mihomo/component/http"
	"github.com/metacubex/mihomo/component/profile/cachefile"
	"github.com/metacubex/mihomo/component/resource"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/listener/inner"
)

func initializeTestTransport() {
	mihomoHTTP.SetUA("clash.meta/" + C.Version)
	resource.SetETag(false)
	inner.New(providerTunnel{})
}

func TestNormalizedProxy(t *testing.T) {
	tests := []struct {
		name string
		raw  string
		want string
	}{
		{name: "empty", raw: "", want: ""},
		{name: "direct", raw: "direct", want: ""},
		{name: "implicit http", raw: "127.0.0.1:7890", want: "http://127.0.0.1:7890"},
		{name: "default http port", raw: "http://proxy.example", want: "http://proxy.example:80"},
		{name: "default socks port", raw: "socks5h://proxy.example", want: "socks5h://proxy.example:1080"},
		{name: "authenticated http", raw: "http://user:pass@127.0.0.1:7890", want: "http://user:pass@127.0.0.1:7890"},
		{name: "socks remote dns", raw: "socks5h://[::1]:1080", want: "socks5h://[::1]:1080"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, err := normalizedProxy(test.raw)
			if err != nil {
				t.Fatalf("normalizedProxy() error = %v", err)
			}
			if got != test.want {
				t.Fatalf("normalizedProxy() = %q, want %q", got, test.want)
			}
		})
	}
}

func TestNormalizedProxyRejectsUnsupportedProfiles(t *testing.T) {
	for _, raw := range []string{
		"ftp://127.0.0.1:21",
		"http://127.0.0.1:7890/path",
		"http://127.0.0.1:70000",
	} {
		if _, err := normalizedProxy(raw); err == nil {
			t.Fatalf("normalizedProxy(%q) unexpectedly succeeded", raw)
		}
	}
}

func TestSocksDNSProfilesRemainDistinct(t *testing.T) {
	if !proxyUsesLocalDNS("socks5://127.0.0.1:1080") {
		t.Fatal("socks5 must retain local DNS semantics")
	}
	if proxyUsesLocalDNS("socks5h://127.0.0.1:1080") {
		t.Fatal("socks5h must retain proxy-side DNS semantics")
	}
}

func TestWindowsPerProtocolProxySelection(t *testing.T) {
	raw := "http=proxy-http.example:8080;https=proxy-https.example:8443;socks=proxy-socks.example:1080"
	tests := []struct {
		requestURL string
		want       string
	}{
		{requestURL: "http://provider.example/sub", want: "http://proxy-http.example:8080"},
		{requestURL: "https://provider.example/sub", want: "http://proxy-https.example:8443"},
	}
	for _, test := range tests {
		got, err := proxyForRequest(raw, test.requestURL)
		if err != nil {
			t.Fatalf("proxyForRequest(%q) error = %v", test.requestURL, err)
		}
		if got != test.want {
			t.Fatalf("proxyForRequest(%q) = %q, want %q", test.requestURL, got, test.want)
		}
	}
}

func TestWindowsProxySelectionFallsBackToSocks(t *testing.T) {
	got, err := proxyForRequest("socks=127.0.0.1:1080", "https://provider.example/sub")
	if err != nil {
		t.Fatalf("proxyForRequest() error = %v", err)
	}
	if got != "socks5://127.0.0.1:1080" {
		t.Fatalf("proxyForRequest() = %q", got)
	}
}

func TestWindowsProxySelectionRejectsMissingDestinationRoute(t *testing.T) {
	if _, err := proxyForRequest("http=127.0.0.1:8080", "https://provider.example/sub"); err == nil {
		t.Fatal("proxyForRequest() unexpectedly accepted a missing HTTPS route")
	}
}

func TestProxyCredentialsContainingEqualsAreNotWindowsProfileSyntax(t *testing.T) {
	raw := "http://user:p=ass@127.0.0.1:8080"
	got, err := proxyForRequest(raw, "https://provider.example/sub")
	if err != nil {
		t.Fatalf("proxyForRequest() error = %v", err)
	}
	if got != raw {
		t.Fatalf("proxyForRequest() = %q, want %q", got, raw)
	}
}

func TestExecuteFetchUsesMihomoProviderTransport(t *testing.T) {
	initializeTestTransport()
	capturedUA := ""
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		capturedUA = request.Header.Get("User-Agent")
		writer.Header().Set("X-Provider-Test", "ok")
		_, _ = writer.Write([]byte("provider-body"))
	}))
	defer server.Close()

	response := executeFetch(fetchRequest{
		Type:      "fetch",
		ID:        7,
		URL:       server.URL,
		TimeoutMS: int64((5 * time.Second) / time.Millisecond),
		SizeLimit: 1024,
	})
	if response.ErrorCode != "" {
		t.Fatalf("executeFetch() error = %s", response.ErrorCode)
	}
	if response.Status != http.StatusOK || string(response.Body) != "provider-body" {
		t.Fatalf("executeFetch() status/body = %d/%q", response.Status, response.Body)
	}
	if capturedUA != "clash.meta/"+C.Version {
		t.Fatalf("User-Agent = %q", capturedUA)
	}
	if values := response.Headers["X-Provider-Test"]; len(values) != 1 || values[0] != "ok" {
		t.Fatalf("response headers were not returned")
	}
}

func TestExecuteFetchPreservesMihomoETagAnd304Semantics(t *testing.T) {
	mihomoHTTP.SetUA("clash.meta/" + C.Version)
	C.SetHomeDir(t.TempDir())
	resource.SetETag(true)
	cache := cachefile.Cache()
	if cache == nil || cache.DB == nil {
		t.Fatal("Mihomo ETag cache did not initialize")
	}
	t.Cleanup(func() {
		resource.SetETag(false)
		_ = cache.Close()
	})

	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		requests++
		if requests == 2 {
			if got := request.Header.Get("If-None-Match"); got != `"provider-v1"` {
				t.Errorf("If-None-Match = %q", got)
			}
			writer.Header().Set("Subscription-UserInfo", "upload=1; download=2; total=3")
			writer.WriteHeader(http.StatusNotModified)
			return
		}
		writer.Header().Set("ETag", `"provider-v1"`)
		_, _ = writer.Write([]byte("etag-provider-body"))
	}))
	defer server.Close()

	first := executeFetch(fetchRequest{ID: 20, URL: server.URL, SizeLimit: 1024})
	if first.ErrorCode != "" || first.Status != http.StatusOK || first.BodyHash == "" {
		t.Fatalf("first fetch = status %d, hash %q, error %q", first.Status, first.BodyHash, first.ErrorCode)
	}
	second := executeFetch(fetchRequest{
		ID:        21,
		URL:       server.URL,
		OldHash:   first.BodyHash,
		SizeLimit: 1024,
	})
	if second.ErrorCode != "" || second.Status != http.StatusNotModified || !second.NotModified {
		t.Fatalf("second fetch = status %d, not_modified %v, error %q", second.Status, second.NotModified, second.ErrorCode)
	}
	if len(second.Body) != 0 || second.BodyHash != first.BodyHash {
		t.Fatalf("304 body/hash = %q/%q, want empty/%q", second.Body, second.BodyHash, first.BodyHash)
	}
	if values := second.Headers["Subscription-Userinfo"]; len(values) != 1 {
		t.Fatalf("304 response headers were not preserved: %#v", second.Headers)
	}
}

func TestExecuteFetchUsesMihomoHTTPProxyOutbound(t *testing.T) {
	initializeTestTransport()
	target := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		_, _ = writer.Write([]byte("proxied-provider-body"))
	}))
	defer target.Close()

	connectSeen := make(chan struct{}, 1)
	proxy := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodConnect {
			http.Error(writer, "CONNECT required", http.StatusMethodNotAllowed)
			return
		}
		upstream, err := net.Dial("tcp", request.Host)
		if err != nil {
			http.Error(writer, "dial failed", http.StatusBadGateway)
			return
		}
		defer upstream.Close()
		client, buffered, err := writer.(http.Hijacker).Hijack()
		if err != nil {
			return
		}
		defer client.Close()
		_, _ = fmt.Fprint(client, "HTTP/1.1 200 Connection Established\r\n\r\n")
		select {
		case connectSeen <- struct{}{}:
		default:
		}
		go func() {
			_, _ = io.Copy(upstream, buffered)
			_ = upstream.Close()
		}()
		_, _ = io.Copy(client, upstream)
	}))
	defer proxy.Close()

	response := executeFetch(fetchRequest{
		Type:      "fetch",
		ID:        8,
		URL:       target.URL,
		Proxy:     strings.TrimPrefix(proxy.URL, "http://"),
		TimeoutMS: int64((5 * time.Second) / time.Millisecond),
		SizeLimit: 1024,
	})
	if response.ErrorCode != "" {
		t.Fatalf("executeFetch() error = %s", response.ErrorCode)
	}
	if string(response.Body) != "proxied-provider-body" {
		t.Fatalf("executeFetch() body = %q", response.Body)
	}
	select {
	case <-connectSeen:
	default:
		t.Fatal("Mihomo HTTP proxy outbound was not used")
	}
}

func TestSafeErrorsNeverEchoSecrets(t *testing.T) {
	secret := "token-userinfo-secret"
	for _, code := range []string{"timeout", "tls_validation", "unsupported_proxy", "invalid_request", "fetch_failed"} {
		if strings.Contains(safeErrorMessage(code), secret) {
			t.Fatalf("safe error for %q leaked a secret", code)
		}
	}
}
