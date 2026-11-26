//go:build linux || darwin

package main

import (
	"bytes"
	"context"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestCheckoutInitialRefreshCLI(t *testing.T) {
	for _, skip := range []bool{false, true} {
		r := cli_NewRunner()
		r.In = strings.NewReader(`{"accessToken":"fixture-at","sessionToken":"fixture-cookie","account":{"id":"acct"},"user":{"email":"a@example.test"}}`)
		r.Out, r.Err = &bytes.Buffer{}, &bytes.Buffer{}
		client := &teamCaptureClient{}
		r.ClientFactory = func(model_Config, gptpay_ClientOptions) cli_Client { return client }
		args := []string{"--country", "PH", "--plan", "plus"}
		if skip {
			args = append(args, "--skip-initial-session-refresh")
		}
		if code := r.CreateCheckout(context.Background(), args); code != 0 {
			t.Fatalf("checkout CLI failed: %d", code)
		}
		if client.options.SkipInitialSessionRefresh != skip {
			t.Fatal("CLI lost initial refresh selection")
		}
	}
}

type checkoutInitialRefreshTransport struct {
	checkoutContinuityTransport
	account401, failSave       bool
	requests, refreshes, saves int
}

func (f *checkoutInitialRefreshTransport) Request(method, target string, opts protocol_RequestOptions) (*protocol_HTTPResponse, error) {
	if f.pending {
		f.t.Fatal("request continued before credential acknowledgement")
	}
	f.requests++
	r := &protocol_HTTPResponse{StatusCode: 200, Headers: http.Header{"Content-Type": {"application/json"}}, Content: []byte(`{}`)}
	if f.requests == 1 {
		r.Content = []byte("<html>fixture</html>")
		r.Headers.Set("Content-Type", "text/html")
		r.Headers.Set("Set-Cookie", "oai-did=fixture-device; Path=/")
		return r, nil
	}
	if checkoutSessionToken(protocol_AsString(opts.Headers.Get("Cookie"))) != f.latestCookie {
		f.t.Fatal("request used stale Session")
	}
	if strings.Contains(target, "/api/auth/session") {
		if !f.account401 || f.requests != 3 {
			f.t.Fatal("disabled initial refresh still ran")
		}
		f.refreshes++
		f.latestAT, f.latestCookie = "refreshed-at", "refreshed-cookie"
		r.Content = []byte(`{"accessToken":"refreshed-at","account":{"id":"acct"}}`)
		r.Headers.Set("Set-Cookie", "__Secure-next-auth.session-token=refreshed-cookie; Path=/")
		f.pending = true
		return r, nil
	}
	if !strings.Contains(target, "/accounts/check/") || opts.Headers.Get("Authorization") != "Bearer "+f.latestAT {
		f.t.Fatal("account query used wrong token or unexpected request")
	}
	r.StatusCode = 400 // Stop before real checkout/payment; the response follows the real error path.
	if f.account401 && f.requests == 2 {
		r.StatusCode = 401
	}
	return r, nil
}

func TestDisabledInitialRefreshRetains401RefreshAndSaveFence(t *testing.T) {
	for _, tc := range []struct{ account401, failSave bool }{{false, false}, {true, false}, {true, true}} {
		tr := &checkoutInitialRefreshTransport{checkoutContinuityTransport: checkoutContinuityTransport{t: t, latestAT: "initial-at", latestCookie: "initial-cookie"}, account401: tc.account401, failSave: tc.failSave}
		client := gptpay_NewPaymentClient(model_DefaultConfig())
		client.transportFactory = func(context.Context, string, float64, time.Time) (protocol_HTTPTransport, error) { return tr, nil }
		ctx := context.WithValue(context.Background(), checkoutCredentialContextKey{}, checkoutCredentialSink(func(s model_Session) error {
			if tc.failSave {
				return protocol_NewError("checkpoint_failed")
			}
			if s.AccessToken != tr.latestAT || s.SessionToken != tr.latestCookie {
				t.Fatal("saved stale credentials")
			}
			tr.saves++
			tr.pending = false
			return nil
		}))
		result, err := client.CreateCheckout(ctx, model_Session{AccessToken: "initial-at", SessionToken: "initial-cookie", AccountID: "acct", Email: "a@example.test"}, gptpay_CreateCheckoutOptions{Country: "PH", Plan: "plus", CheckoutType: "hosted", SkipInitialSessionRefresh: true})
		if err != nil {
			t.Fatal(err)
		}
		requests, refreshes, saves, code := 2, 0, 0, "http_400"
		if tc.account401 {
			requests, refreshes, saves = 4, 1, 1
		}
		if tc.failSave {
			requests, saves, code = 3, 0, "checkpoint_failed"
		}
		if tr.requests != requests || tr.refreshes != refreshes || tr.saves != saves || result.ErrorCode != code {
			t.Fatalf("incorrect refresh/save boundary: requests=%d refreshes=%d saves=%d code=%s", tr.requests, tr.refreshes, tr.saves, result.ErrorCode)
		}
	}
}
