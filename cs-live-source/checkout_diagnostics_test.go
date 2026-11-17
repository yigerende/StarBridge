//go:build linux || darwin

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"reflect"
	"strings"
	"sync"
	"testing"
)

func TestCheckoutRefreshEvidencePreservesProtocol(t *testing.T) {
	for _, test := range []struct{ body, want string }{
		{`{"accessToken":"new-private-token","account":{"id":"acct"}}`, ""},
		{`{"error":"RefreshAccessTokenError"}`, "session_expired"},
		{`{}`, "session_expired"},
		{`{"accessToken":"new-private-token","account":{"id":"other"}}`, "account_mismatch"},
	} {
		t.Run(test.want+test.body[:2], func(t *testing.T) {
			var states []model_Checkpoint
			for _, enabled := range []bool{false, true} {
				cp := model_DefaultCheckpoint()
				cp.AccessToken, cp.SessionToken, cp.AccountID = "old-private-token", "private-session", "acct"
				cp.Cookies = "__Secure-next-auth.session-token=old-private-cookie"
				clock, _ := fingerprint_NewClock(0)
				p := chatgpt_NewPlatform(&homepageTransport{&protocol_HTTPResponse{StatusCode: 200,
					Headers: http.Header{"Content-Type": {"application/json"}, "Set-Cookie": {"__Secure-next-auth.session-token=new-private-cookie; Path=/"}},
					Content: []byte(test.body)}}, &cp, clock)
				d := newCheckoutDiagnostics(enabled, strings.Repeat("a", 64))
				p.Diagnostics = d
				err := p.Refresh()
				if protocol_FlowErrorCode(err) != test.want {
					t.Fatalf("result changed: %v", err)
				}
				states = append(states, cp)
				if !enabled {
					continue
				}
				var buffer bytes.Buffer
				d.write(&buffer)
				for _, secret := range []string{"old-private-token", "new-private-token", "private-session", "old-private-cookie", "new-private-cookie", "acct"} {
					if strings.Contains(buffer.String(), secret) {
						t.Fatal("credential leaked")
					}
				}
				event := d.events[0]
				if event["auth_cookie_version_changed"] != true {
					t.Fatal("cookie rotation evidence lost")
				}
				if test.want == "" && event["at_version_changed"] != true {
					t.Fatal("AT change lost")
				}
				if strings.Contains(test.body, "RefreshAccessTokenError") && event["response_error"] != "RefreshAccessTokenError" {
					t.Fatal("upstream error lost")
				}
			}
			// Metadata includes a random UI session identifier; auth state must match.
			if states[0].AccessToken != states[1].AccessToken || states[0].SessionToken != states[1].SessionToken || states[0].Cookies != states[1].Cookies {
				t.Fatal("diagnostics modified credentials")
			}
		})
	}
}

func TestCheckoutDiagnosticsBoundedAndPrivate(t *testing.T) {
	d := newCheckoutDiagnostics(true, strings.Repeat("b", 64))
	d.add("input_credentials", protocol_Map{"has_at": true})
	var group sync.WaitGroup
	for i := 0; i < 10; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			for n := 0; n < 100; n++ {
				d.event(model_Event{"event": "http_request", "path": "/api/auth/session?token=PRIVATE",
					"responseMessage": "PRIVATE", "responseErrorCode": "PRIVATE", "status": int64(400), "durationMs": 12.0, "method": "GET"})
			}
		}()
	}
	group.Wait()
	if len(d.events) != 192 || d.dropped != 809 {
		t.Fatalf("unbounded collector: %d/%d", len(d.events), d.dropped)
	}
	if d.events[0]["event"] != "input_credentials" {
		t.Fatal("initial evidence lost")
	}
	var buffer bytes.Buffer
	d.write(&buffer)
	if strings.Contains(buffer.String(), "PRIVATE") {
		t.Fatal("arbitrary text leaked")
	}
	if len(buffer.Bytes()) > 128*1024 {
		t.Fatal("frame exceeds reader cap")
	}
	if d.events[1]["durationMs"] != 12.0 {
		t.Fatal("elapsed time lost")
	}
	if d.version("token") != newCheckoutDiagnostics(true, strings.Repeat("b", 64)).version("token") || d.version("token") == newCheckoutDiagnostics(true, strings.Repeat("c", 64)).version("token") {
		t.Fatal("version scoping broken")
	}
}

type diagnosticCLIClient struct {
	teamCaptureClient
	sink model_EventSink
}

func (c *diagnosticCLIClient) CreateCheckout(ctx context.Context, s model_Session, o gptpay_CreateCheckoutOptions) (model_CheckoutResult, error) {
	if ctx.Value(checkoutDiagnosticContextKey{}) == nil {
		return model_CheckoutResult{}, errors.New("missing diagnostic context")
	}
	c.sink(model_Event{"event": "http_request", "path": "/backend-api/payments/checkout", "status": int64(400), "errorCode": "http_400"})
	return model_CheckoutResult{ErrorCode: "http_400"}, nil
}
func TestCheckoutDiagnosticCLISeparatesResults(t *testing.T) {
	out, stderr := &bytes.Buffer{}, &bytes.Buffer{}
	r := cli_NewRunner()
	r.Out = out
	r.Err = stderr
	r.SessionInput = func(context.Context, string) (model_Session, error) { return model_Session{}, nil }
	r.ClientFactory = func(c model_Config, options gptpay_ClientOptions) cli_Client {
		return &diagnosticCLIClient{sink: options.EventSink}
	}
	if r.CreateCheckout(context.Background(), []string{"--country", "PH", "--plan", "pro5x", "--diagnostics-json"}) != 1 {
		t.Fatal("failure result changed")
	}
	var result, evidence map[string]any
	if json.Unmarshal(out.Bytes(), &result) != nil || json.Unmarshal(stderr.Bytes(), &evidence) != nil {
		t.Fatal("invalid output channels")
	}
	if result["error_code"] != "http_400" || evidence["starbridge_checkout_diagnostics"] != float64(1) {
		t.Fatal("missing evidence")
	}
	if _, ok := result["events"]; ok {
		t.Fatal("private details in public result")
	}
}

func TestAuthCookieComparisonIgnoresOrderAndNonAuthCookies(t *testing.T) {
	a := checkoutAuthCookies("oai-did=one; __Secure-next-auth.session-token.0=A; __Secure-next-auth.session-token.1=B")
	b := checkoutAuthCookies("__Secure-next-auth.session-token.1=B; oai-did=two; __Secure-next-auth.session-token.0=A")
	if !reflect.DeepEqual(a, b) {
		t.Fatal("non-auth cookie change mislabeled")
	}
}

func BenchmarkCheckoutDiagnosticCollection(b *testing.B) {
	for i := 0; i < b.N; i++ {
		d := newCheckoutDiagnostics(true, strings.Repeat("a", 64))
		for n := 0; n < 30; n++ {
			d.event(model_Event{"event": "http_request", "path": "/api/auth/session", "status": int64(200), "durationMs": 12.0, "method": "GET"})
		}
		var output bytes.Buffer
		d.write(&output)
	}
}
