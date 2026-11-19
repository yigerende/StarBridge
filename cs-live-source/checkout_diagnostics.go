//go:build linux || darwin

package main

// Diagnostics collect bounded facts in memory. They never issue requests, save
// checkpoints, or change request/refresh decisions. One frame is written at exit.
import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"math"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

type checkoutDiagnosticContextKey struct{}
type checkoutDiagnostics struct {
	mu      sync.Mutex
	key     []byte
	started time.Time
	events  []protocol_Map
	dropped int
	details int
}

func (d *checkoutDiagnostics) reserveDetail() bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.details >= 24 {
		return false
	}
	d.details++
	return true
}

func newCheckoutDiagnostics(enabled bool, key string) *checkoutDiagnostics {
	if !enabled {
		return nil
	}
	decoded, err := hex.DecodeString(key)
	if err != nil || len(decoded) != 32 {
		decoded = nil
	}
	return &checkoutDiagnostics{key: decoded, started: time.Now()}
}

func (d *checkoutDiagnostics) version(value string) string {
	if len(d.key) == 0 || value == "" {
		return ""
	}
	h := hmac.New(sha256.New, d.key)
	h.Write([]byte(value))
	return hex.EncodeToString(h.Sum(nil))[:24]
}

func checkoutAuthCookies(raw string) string {
	request := &http.Request{Header: http.Header{"Cookie": []string{raw}}}
	var values []string
	for _, c := range request.Cookies() {
		for _, name := range []string{"__Secure-next-auth.session-token", "__Secure-platform.session-token"} {
			if c.Name == name || strings.HasPrefix(c.Name, name+".") {
				values = append(values, c.Name+"="+c.Value)
			}
		}
	}
	sort.Strings(values)
	return strings.Join(values, ";")
}

func (d *checkoutDiagnostics) auth(cp *model_Checkpoint) protocol_Map {
	return protocol_Map{"at_version": d.version(cp.AccessToken), "session_version": d.version(cp.SessionToken),
		"proxy_version":       d.version(cp.Proxy),
		"auth_cookie_version": d.version(checkoutAuthCookies(cp.Cookies)),
		"has_at":              cp.AccessToken != "", "has_session": cp.SessionToken != ""}
}

// Only fingerprints enter the collector. This is bounded in-memory work, with
// no persistence, requests, waits, or changes to the headers being observed.
func (d *checkoutDiagnostics) credentials(cp *model_Checkpoint, step, point string) {
	fields := d.auth(cp)
	fields["step"], fields["point"] = checkoutDiagnosticStep(step), point
	d.add("credentials", fields)
}

func (d *checkoutDiagnostics) wire(path, authorization string, cookies []string) {
	raw := strings.Join(cookies, "; ")
	seen := map[string]bool{}
	duplicate := false
	request := &http.Request{Header: http.Header{"Cookie": []string{raw}}}
	for _, c := range request.Cookies() {
		if strings.HasPrefix(c.Name, "__Secure-next-auth.session-token") || strings.HasPrefix(c.Name, "__Secure-platform.session-token") {
			duplicate = duplicate || seen[c.Name]
			seen[c.Name] = true
		}
	}
	d.add("wire_credentials", protocol_Map{"step": checkoutDiagnosticStep(path),
		"at_version":          d.version(strings.TrimPrefix(authorization, "Bearer ")),
		"auth_cookie_version": d.version(checkoutAuthCookies(raw)),
		"has_at":              authorization != "", "duplicate_auth_cookies": duplicate})
}

func checkoutDiagnosticCode(value string) string {
	if value == "" {
		return ""
	}
	if observability_knownErrors[value] || observability_providerCodes[value] || protocol_Matches(`http_[1-5][0-9]{2}`, value) {
		return value
	}
	if protocol_StringSet("RefreshAccessTokenError", "RefreshTokenError", "OAuthCallbackError", "OAuthSessionExpired",
		"SessionExpired", "invalid_grant", "invalid_token", "invalid_request", "invalid_request_error", "authentication_error",
		"token_expired", "session_revoked", "invalid_session", "account_deactivated", "account_disabled", "access_denied")[value] {
		return value
	}
	return "other"
}

func (d *checkoutDiagnostics) refresh(before protocol_Map, cp *model_Checkpoint, data protocol_Map, err error) {
	fields := d.auth(cp)
	for _, key := range []string{"at_version", "session_version", "auth_cookie_version"} {
		fields["before_"+key] = before[key]
		fields[key+"_changed"] = before[key] != fields[key]
	}
	fields["error_code"] = checkoutDiagnosticCode(protocol_FlowErrorCode(err))
	fields["outcome"] = "succeeded"
	if err != nil {
		fields["outcome"] = "failed"
	}
	fields["response_has_at"] = protocol_AsString(data["accessToken"]) != ""
	fields["response_has_error"] = protocol_Truthy(data["error"])
	fields["response_error"] = checkoutDiagnosticCode(protocol_AsString(data["error"]))
	if nested, ok := data["error"].(map[string]any); ok {
		fields["response_error"] = checkoutDiagnosticCode(protocol_AsString(nested["code"]))
	}
	account := protocol_AsString(protocol_AsMap(data["account"])["id"])
	fields["response_has_account"] = account != ""
	fields["account_matches"] = account != "" && account == cp.AccountID
	d.add("session_refresh", fields)
}

func (d *checkoutDiagnostics) add(event string, fields protocol_Map) {
	fields["event"] = event
	fields["elapsed_ms"] = time.Since(d.started).Milliseconds()
	d.mu.Lock()
	defer d.mu.Unlock()
	// Keep early input/session evidence and the latest failure if a flow is noisy.
	if len(d.events) >= 192 {
		copy(d.events[96:], d.events[97:])
		d.events[len(d.events)-1] = fields
		d.dropped++
	} else {
		d.events = append(d.events, fields)
	}
}

func checkoutDiagnosticStep(path string) string {
	switch {
	case path == "/":
		return "homepage"
	case path == "/api/auth/session":
		return "session_refresh"
	case strings.Contains(path, "/accounts/check"):
		return "account_check"
	case strings.Contains(path, "/subscriptions"):
		return "subscription_check"
	case path == "/backend-api/payments/checkout":
		return "checkout_create"
	case strings.Contains(path, "/sentinel/"):
		return "verification"
	case strings.Contains(path, "/payment_pages/"):
		return "checkout_price"
	case strings.Contains(path, "/stripe_client_bootstrap"):
		return "payment_bootstrap"
	default:
		return "other"
	}
}

func (d *checkoutDiagnostics) event(event model_Event) {
	name, _ := event["event"].(string)
	if !protocol_StringSet("http_request", "http_retry_wait", "phase", "transaction", "price", "checkpoint")[name] {
		return
	}
	fields := protocol_Map{}
	for _, key := range []string{"attempt", "status", "durationMs", "remainingDeadlineMs", "connectionReadyMs", "requestWriteMs", "firstByteMs", "responseWaitMs", "retryWaitElapsedMs"} {
		if value, ok := event[key].(float64); ok && !math.IsNaN(value) && value >= 0 && value <= 3600000 {
			fields[key] = value
		} else if value, ok := protocol_Integer(event[key]); ok && value >= 0 && value <= 3600000 {
			fields[key] = value
		}
	}
	for key, allowed := range map[string]map[string]bool{
		"method":  protocol_StringSet("GET", "POST", "PATCH", "DELETE", "PUT"),
		"service": protocol_StringSet("chatgpt", "stripe"),
		"outcome": protocol_StringSet("started", "completed", "succeeded", "failed", "retry", "retrying", "received", "saved", "skipped"),
		"phase":   protocol_StringSet("initializing", "account_checked", "stripe_ready", "checkout_created", "checkout_ready"),
	} {
		if value, ok := event[key].(string); ok && allowed[value] {
			fields[key] = value
		}
	}
	for _, key := range []string{"errorCode", "responseErrorCode", "responseErrorType"} {
		if value, ok := event[key].(string); ok {
			fields[key] = checkoutDiagnosticCode(value)
		}
	}
	// Transport has already removed known request/proxy/cookie secrets. Keep
	// descriptive error fields separately from the business error classifier.
	if (event["outcome"] == "failed" || event["outcome"] == "retry") && d.reserveDetail() {
		fields["upstream_content_type"] = observability_safeContentType(protocol_AsString(event["responseContentType"]))
		for key, target := range map[string]string{"responseErrorCode": "upstream_code", "responseErrorType": "upstream_type",
			"responseMessage": "upstream_message", "responseDetail": "upstream_detail", "responseTitle": "upstream_title", "responseSummary": "upstream_summary"} {
			if value, ok := event[key].(string); ok {
				if target == "upstream_code" || target == "upstream_type" {
					fields[target] = observability_safeResponseCode(value, nil)
				} else {
					fields[target] = observability_SafeResponseText(value, nil)
				}
			}
		}
	}
	if value, ok := event["responseRequestId"].(string); ok && observability_diagnosticRequestID.MatchString(value) {
		fields["upstream_request_id"] = value
	}
	if path, ok := event["path"].(string); ok {
		fields["step"] = checkoutDiagnosticStep(path)
	}
	d.add(name, fields)
}

func (d *checkoutDiagnostics) write(w io.Writer) {
	// No error here can change the checkout result or start another request.
	defer func() { _ = recover() }()
	d.mu.Lock()
	defer d.mu.Unlock()
	_ = json.NewEncoder(w).Encode(protocol_Map{"starbridge_checkout_diagnostics": 1, "events": d.events, "dropped": d.dropped})
}
