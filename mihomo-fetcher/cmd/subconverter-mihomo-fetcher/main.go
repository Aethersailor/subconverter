package main

import (
	"bufio"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/fxamacker/cbor/v2"
	metaHTTP "github.com/metacubex/http"
	"github.com/metacubex/mihomo/adapter/outbound"
	N "github.com/metacubex/mihomo/common/net"
	"github.com/metacubex/mihomo/common/utils"
	mihomoHTTP "github.com/metacubex/mihomo/component/http"
	"github.com/metacubex/mihomo/component/profile/cachefile"
	"github.com/metacubex/mihomo/component/proxydialer"
	"github.com/metacubex/mihomo/component/resolver"
	"github.com/metacubex/mihomo/component/resource"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/listener/inner"
	logrus "github.com/sirupsen/logrus"
)

const (
	protocolVersion  = uint64(1)
	maxRequestFrame  = 4 << 20
	maxResponseFrame = 128 << 20
	maxBodyPayload   = 120 << 20
	maxConcurrency   = 8
)

// These values are injected by the locked build. Unknown values are rejected by
// the C++ parent and therefore cannot silently masquerade as a release build.
var (
	mihomoCommit = "unknown"
	overlayHash  = "unknown"
)

type envelope struct {
	Type string `cbor:"type"`
}

type hello struct {
	Type             string   `cbor:"type"`
	Protocol         uint64   `cbor:"protocol"`
	MihomoVersion    string   `cbor:"mihomo_version"`
	MihomoCommit     string   `cbor:"mihomo_commit"`
	OverlaySHA256    string   `cbor:"overlay_sha256"`
	GoVersion        string   `cbor:"go_version"`
	GOOS             string   `cbor:"goos"`
	GOARCH           string   `cbor:"goarch"`
	DefaultUserAgent string   `cbor:"default_user_agent"`
	Capabilities     []string `cbor:"capabilities"`
}

type fetchRequest struct {
	Type      string              `cbor:"type"`
	ID        uint64              `cbor:"id"`
	URL       string              `cbor:"url"`
	Headers   map[string][]string `cbor:"headers,omitempty"`
	Proxy     string              `cbor:"proxy,omitempty"`
	OldHash   string              `cbor:"old_hash,omitempty"`
	TimeoutMS int64               `cbor:"timeout_ms,omitempty"`
	SizeLimit int64               `cbor:"size_limit,omitempty"`
}

type fetchResponse struct {
	Type         string              `cbor:"type"`
	ID           uint64              `cbor:"id"`
	Status       int                 `cbor:"status"`
	FinalURL     string              `cbor:"final_url,omitempty"`
	Headers      map[string][]string `cbor:"headers,omitempty"`
	Body         []byte              `cbor:"body,omitempty"`
	BodyHash     string              `cbor:"body_hash,omitempty"`
	NotModified  bool                `cbor:"not_modified,omitempty"`
	ErrorCode    string              `cbor:"error_code,omitempty"`
	ErrorMessage string              `cbor:"error_message,omitempty"`
}

type frameWriter struct {
	mu sync.Mutex
	w  *bufio.Writer
}

// providerTunnel gives HTTPVehicle the same inner-tunnel path used by a running
// Mihomo instance while keeping the helper deliberately limited to DIRECT and
// the standard proxy URL formats already accepted by subconverter.
type providerTunnel struct{}

func (providerTunnel) NatTable() C.NatTable {
	return nil
}

func (providerTunnel) HandleUDPPacket(packet C.UDPPacket, _ *C.Metadata) {
	packet.Drop()
}

func (providerTunnel) HandleTCPConn(local net.Conn, metadata *C.Metadata) {
	defer local.Close()

	ctx, cancel := context.WithTimeout(context.Background(), C.DefaultTCPTimeout)
	defer cancel()
	dialMetadata := metadata
	var adapter C.ProxyAdapter
	var err error
	if metadata.SpecialProxy == "" {
		adapter = outbound.NewDirect()
	} else {
		adapter, err = newProxyAdapter(metadata.SpecialProxy)
		if err == nil && proxyUsesLocalDNS(metadata.SpecialProxy) && metadata.Host != "" {
			var resolved net.IP
			ip, resolveErr := resolver.ResolveIPWithResolver(ctx, metadata.Host, resolver.DirectHostResolver)
			if resolveErr != nil {
				err = resolveErr
			} else {
				resolved = net.IP(ip.AsSlice())
				copyMetadata := *metadata
				if setErr := copyMetadata.SetRemoteAddress(net.JoinHostPort(resolved.String(), strconv.Itoa(int(metadata.DstPort)))); setErr != nil {
					err = setErr
				} else {
					dialMetadata = &copyMetadata
				}
			}
		}
	}
	if err != nil {
		return
	}
	remote, err := proxydialer.New(adapter, false).DialContext(ctx, "tcp", dialMetadata.RemoteAddress())
	if err != nil {
		return
	}
	defer remote.Close()
	N.Relay(local, remote)
}

func proxyUsesLocalDNS(raw string) bool {
	normalized, err := normalizedProxy(raw)
	if err != nil || normalized == "" {
		return false
	}
	parsed, err := url.Parse(normalized)
	return err == nil && strings.EqualFold(parsed.Scheme, "socks5")
}

func normalizedProxy(raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" || strings.EqualFold(raw, "DIRECT") {
		return "", nil
	}
	if !strings.Contains(raw, "://") {
		raw = "http://" + raw
	}
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Hostname() == "" {
		return "", errors.New("invalid proxy profile")
	}
	if parsed.Path != "" && parsed.Path != "/" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", errors.New("invalid proxy profile")
	}
	defaultPort := ""
	switch strings.ToLower(parsed.Scheme) {
	case "http":
		defaultPort = "80"
	case "https":
		defaultPort = "443"
	case "socks5", "socks5h":
		defaultPort = "1080"
	default:
		return "", errors.New("unsupported proxy profile")
	}
	if parsed.Port() == "" {
		parsed.Host = net.JoinHostPort(parsed.Hostname(), defaultPort)
	}
	port, err := strconv.ParseUint(parsed.Port(), 10, 16)
	if err != nil || port == 0 {
		return "", errors.New("invalid proxy profile")
	}
	return parsed.String(), nil
}

// proxyForRequest translates the per-protocol form emitted by the Windows
// Internet Settings registry (for example, http=host:port;https=host:port)
// before it crosses the Mihomo HTTPVehicle boundary. The protocol key names
// the destination scheme; WinINet uses an HTTP proxy for both http= and
// https= entries unless the endpoint itself explicitly carries a scheme.
func proxyForRequest(raw string, requestURL string) (string, error) {
	raw = strings.TrimSpace(raw)
	separator := strings.IndexByte(raw, '=')
	profileKey := ""
	if separator >= 0 {
		profileKey = strings.ToLower(strings.TrimSpace(raw[:separator]))
	}
	isPerProtocol := profileKey == "http" || profileKey == "https" ||
		profileKey == "socks" || profileKey == "socks5" || profileKey == "ftp"
	if raw == "" || strings.EqualFold(raw, "DIRECT") || !isPerProtocol {
		return normalizedProxy(raw)
	}

	destination, err := url.Parse(requestURL)
	if err != nil || destination.Scheme == "" {
		return "", errors.New("invalid subscription URL")
	}
	profiles := make(map[string]string)
	for _, item := range strings.Split(raw, ";") {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		key, value, found := strings.Cut(item, "=")
		key = strings.ToLower(strings.TrimSpace(key))
		value = strings.TrimSpace(value)
		if !found || value == "" || profiles[key] != "" {
			return "", errors.New("invalid proxy profile")
		}
		switch key {
		case "http", "https", "socks", "socks5":
			profiles[key] = value
		default:
			// Ignore profiles for destination protocols that this helper never
			// fetches, such as legacy FTP entries.
		}
	}

	selected := profiles[strings.ToLower(destination.Scheme)]
	selectedScheme := "http"
	if selected == "" {
		selected = profiles["socks5"]
		selectedScheme = "socks5"
	}
	if selected == "" {
		selected = profiles["socks"]
		selectedScheme = "socks5"
	}
	if selected == "" {
		return "", errors.New("proxy profile has no route for subscription URL")
	}
	if !strings.Contains(selected, "://") {
		selected = selectedScheme + "://" + selected
	}
	return normalizedProxy(selected)
}

func newProxyAdapter(raw string) (C.ProxyAdapter, error) {
	normalized, err := normalizedProxy(raw)
	if err != nil || normalized == "" {
		return nil, errors.New("invalid proxy profile")
	}
	parsed, err := url.Parse(normalized)
	if err != nil {
		return nil, errors.New("invalid proxy profile")
	}
	port, err := strconv.Atoi(parsed.Port())
	if err != nil {
		return nil, errors.New("invalid proxy profile")
	}
	username := ""
	password := ""
	if parsed.User != nil {
		username = parsed.User.Username()
		password, _ = parsed.User.Password()
	}

	switch strings.ToLower(parsed.Scheme) {
	case "http", "https":
		return outbound.NewHttp(outbound.HttpOption{
			Name:     "SUBCONVERTER-PROVIDER-PROXY",
			Server:   parsed.Hostname(),
			Port:     port,
			UserName: username,
			Password: password,
			TLS:      strings.EqualFold(parsed.Scheme, "https"),
		})
	case "socks5", "socks5h":
		return outbound.NewSocks5(outbound.Socks5Option{
			Name:     "SUBCONVERTER-PROVIDER-PROXY",
			Server:   parsed.Hostname(),
			Port:     port,
			UserName: username,
			Password: password,
		})
	default:
		return nil, errors.New("unsupported proxy profile")
	}
}

func (w *frameWriter) write(value any) error {
	payload, err := cbor.Marshal(value)
	if err != nil {
		return err
	}
	if len(payload) > maxResponseFrame {
		return fmt.Errorf("response frame exceeds limit")
	}

	w.mu.Lock()
	defer w.mu.Unlock()

	var size [4]byte
	binary.BigEndian.PutUint32(size[:], uint32(len(payload)))
	if _, err = w.w.Write(size[:]); err != nil {
		return err
	}
	if _, err = w.w.Write(payload); err != nil {
		return err
	}
	return w.w.Flush()
}

func readFrame(r *bufio.Reader) ([]byte, error) {
	var size [4]byte
	if _, err := io.ReadFull(r, size[:]); err != nil {
		return nil, err
	}
	length := binary.BigEndian.Uint32(size[:])
	if length == 0 || length > maxRequestFrame {
		return nil, fmt.Errorf("invalid request frame size")
	}
	payload := make([]byte, length)
	_, err := io.ReadFull(r, payload)
	return payload, err
}

func resolveDataDir() (string, error) {
	if configured := os.Getenv("SUBCONVERTER_MIHOMO_DATA_DIR"); configured != "" {
		return filepath.Abs(configured)
	}
	root, err := os.UserCacheDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(root, "subconverter", "mihomo-fetcher"), nil
}

func validateRequest(request fetchRequest) error {
	parsed, err := url.Parse(request.URL)
	if err != nil {
		return errors.New("invalid subscription URL")
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return errors.New("unsupported subscription URL scheme")
	}
	if parsed.Host == "" {
		return errors.New("subscription URL has no host")
	}
	if _, err = proxyForRequest(request.Proxy, request.URL); err != nil {
		return err
	}
	if request.TimeoutMS < 0 || request.SizeLimit < 0 {
		return errors.New("negative request limit")
	}
	return nil
}

func errorCode(err error) string {
	switch {
	case errors.Is(err, context.DeadlineExceeded):
		return "timeout"
	case strings.Contains(strings.ToLower(err.Error()), "certificate") ||
		strings.Contains(strings.ToLower(err.Error()), "x509"):
		return "tls_validation"
	default:
		return "fetch_failed"
	}
}

func safeErrorMessage(code string) string {
	switch code {
	case "timeout":
		return "subscription request timed out"
	case "tls_validation":
		return "subscription TLS validation failed"
	case "unsupported_proxy":
		return "subscription proxy profile is not supported by strict mode"
	case "invalid_request":
		return "invalid subscription request"
	case "response_too_large":
		return "subscription response exceeds the strict transport limit"
	default:
		return "subscription request failed"
	}
}

func executeFetch(request fetchRequest) fetchResponse {
	response := fetchResponse{Type: "response", ID: request.ID}
	if err := validateRequest(request); err != nil {
		response.ErrorCode = "invalid_request"
		if strings.Contains(err.Error(), "proxy") {
			response.ErrorCode = "unsupported_proxy"
		}
		response.ErrorMessage = safeErrorMessage(response.ErrorCode)
		return response
	}

	var oldHash utils.HashType
	if request.OldHash != "" {
		if err := oldHash.UnmarshalText([]byte(request.OldHash)); err != nil {
			response.ErrorCode = "invalid_request"
			response.ErrorMessage = safeErrorMessage(response.ErrorCode)
			return response
		}
	}

	timeout := resource.DefaultHttpTimeout
	if request.TimeoutMS > 0 {
		timeout = time.Duration(request.TimeoutMS) * time.Millisecond
	}

	proxy, err := proxyForRequest(request.Proxy, request.URL)
	if err != nil {
		response.ErrorCode = "unsupported_proxy"
		response.ErrorMessage = safeErrorMessage(response.ErrorCode)
		return response
	}

	sizeLimit := request.SizeLimit
	if sizeLimit == 0 || sizeLimit > maxBodyPayload {
		sizeLimit = maxBodyPayload + 1
	}
	vehicle := resource.NewHTTPVehicle(
		request.URL,
		"",
		proxy,
		metaHTTP.Header(request.Headers),
		timeout,
		sizeLimit,
	)
	vehicle.SetInRead(func(received *metaHTTP.Response) {
		response.Status = received.StatusCode
		response.Headers = map[string][]string(received.Header.Clone())
		if received.Request != nil && received.Request.URL != nil {
			response.FinalURL = received.Request.URL.String()
		}
	})

	body, hash, err := vehicle.Read(context.Background(), oldHash)
	if err != nil {
		response.ErrorCode = errorCode(err)
		response.ErrorMessage = safeErrorMessage(response.ErrorCode)
		return response
	}
	if len(body) > maxBodyPayload {
		response.ErrorCode = "response_too_large"
		response.ErrorMessage = safeErrorMessage(response.ErrorCode)
		return response
	}
	response.NotModified = response.Status == metaHTTP.StatusNotModified
	response.Body = body
	if hash.IsValid() {
		response.BodyHash = hash.String()
	}
	return response
}

func run() error {
	// stdout is the framed IPC transport. Mihomo's package logger defaults to
	// stdout, so suppress it before any cache/resource code can emit a record.
	logrus.SetOutput(io.Discard)

	dataDir, err := resolveDataDir()
	if err != nil {
		return fmt.Errorf("resolve data directory: %w", err)
	}
	if err = os.MkdirAll(dataDir, 0o700); err != nil {
		return fmt.Errorf("create data directory: %w", err)
	}
	if runtime.GOOS != "windows" {
		if err = os.Chmod(dataDir, 0o700); err != nil {
			return fmt.Errorf("secure data directory: %w", err)
		}
	}

	C.SetHomeDir(dataDir)
	defaultUA := "clash.meta/" + C.Version
	mihomoHTTP.SetUA(defaultUA)
	resource.SetETag(true)
	inner.New(providerTunnel{})
	cache := cachefile.Cache()
	if cache == nil || cache.DB == nil {
		return errors.New("initialize strict ETag cache")
	}
	defer cache.Close()

	reader := bufio.NewReader(os.Stdin)
	writer := &frameWriter{w: bufio.NewWriter(os.Stdout)}
	if err = writer.write(hello{
		Type:             "hello",
		Protocol:         protocolVersion,
		MihomoVersion:    C.Version,
		MihomoCommit:     mihomoCommit,
		OverlaySHA256:    overlayHash,
		GoVersion:        runtime.Version(),
		GOOS:             runtime.GOOS,
		GOARCH:           runtime.GOARCH,
		DefaultUserAgent: defaultUA,
		Capabilities:     []string{"direct", "http-proxy", "https-proxy", "socks5-proxy", "etag", "raw-body", "response-headers"},
	}); err != nil {
		return err
	}

	requestSlots := make(chan struct{}, maxConcurrency)
	for {
		payload, readErr := readFrame(reader)
		if errors.Is(readErr, io.EOF) || errors.Is(readErr, io.ErrUnexpectedEOF) {
			return nil
		}
		if readErr != nil {
			return readErr
		}

		var kind envelope
		if err = cbor.Unmarshal(payload, &kind); err != nil || kind.Type != "fetch" {
			return errors.New("invalid request frame")
		}
		var request fetchRequest
		if err = cbor.Unmarshal(payload, &request); err != nil {
			return errors.New("invalid fetch request")
		}
		requestSlots <- struct{}{}
		go func() {
			defer func() { <-requestSlots }()
			if writeErr := writer.write(executeFetch(request)); writeErr != nil {
				os.Exit(1)
			}
		}()
	}
}

func main() {
	if err := run(); err != nil {
		// Never include a request URL or request headers in process-level errors.
		_, _ = fmt.Fprintln(os.Stderr, "mihomo fetcher terminated:", err)
		os.Exit(1)
	}
}
