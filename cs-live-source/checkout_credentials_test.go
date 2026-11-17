//go:build linux || darwin

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestCheckoutCredentialsSaveOnlySuccessfulMatchingRefresh(t *testing.T) {
	for _, tc := range []struct {
		body, code string
		failSave   bool
	}{
		{`{"accessToken":"new-at","account":{"id":"acct"},"user":{"email":"a@example.test"}}`, "", false},
		{`{"accessToken":"new-at","error":"RefreshAccessTokenError"}`, "session_expired", false},
		{`{"accessToken":"new-at","account":{"id":"other"}}`, "account_mismatch", false},
		{`{"accessToken":"new-at","user":{"email":"other@example.test"}}`, "account_mismatch", false},
		{`{"accessToken":"new-at"}`, "checkpoint_failed", true},
	} {
		t.Run(tc.code+tc.body[:10], func(t *testing.T) {
			cp := model_DefaultCheckpoint()
			cp.AccessToken, cp.SessionToken, cp.AccountID, cp.Email = "old-at", "old-cookie", "acct", "a@example.test"
			cp.Cookies = "__Secure-next-auth.session-token=old-cookie; oai-did=device"
			clock, _ := fingerprint_NewClock(0)
			p := chatgpt_NewPlatform(&homepageTransport{&protocol_HTTPResponse{StatusCode: 200,
				Headers: http.Header{"Set-Cookie": {"__Secure-next-auth.session-token.0=new-; Path=/", "__Secure-next-auth.session-token.1=cookie; Path=/"}}, Content: []byte(tc.body)}}, &cp, clock)
			var saved []model_Session
			p.SaveCredentials = func(s model_Session) error {
				if tc.failSave {
					return errors.New("private error")
				}
				saved = append(saved, s)
				return nil
			}
			err := p.Refresh()
			if protocol_FlowErrorCode(err) != tc.code {
				t.Fatalf("unexpected error: %v", err)
			}
			if tc.code == "" {
				if len(saved) != 1 || saved[0].AccessToken != "new-at" || saved[0].SessionToken != "new-cookie" || strings.Contains(saved[0].Cookies, "old-cookie") {
					t.Fatal("latest credentials not saved")
				}
				if cp.AccessToken != saved[0].AccessToken || cp.SessionToken != saved[0].SessionToken {
					t.Fatal("same round uses stale state")
				}
			} else if len(saved) != 0 || cp.AccessToken != "old-at" || cp.SessionToken != "old-cookie" || !strings.Contains(cp.Cookies, "old-cookie") {
				t.Fatal("failed refresh overwrote last valid state")
			}
		})
	}
}

type checkoutContinuityTransport struct {
	t                      *testing.T
	step                   int
	latestAT, latestCookie string
	pending                bool
}

func (f *checkoutContinuityTransport) Request(method, target string, options protocol_RequestOptions) (*protocol_HTTPResponse, error) {
	if f.pending {
		f.t.Fatal("checkout continued before credential save")
	}
	if f.step > 0 && checkoutSessionToken(protocol_AsString(options.Headers.Get("Cookie"))) != f.latestCookie {
		f.t.Fatal("checkout used stale session cookie")
	}
	f.step++
	response := &protocol_HTTPResponse{StatusCode: 200, Headers: http.Header{"Content-Type": {"application/json"}}, Content: []byte(`{}`)}
	switch f.step {
	case 1:
		response.Content = []byte("<html>fixture</html>")
		response.Headers["Content-Type"] = []string{"text/html"}
		response.Headers["Set-Cookie"] = []string{"oai-did=fixture-device; Path=/"}
	case 2, 4:
		if !strings.Contains(target, "/api/auth/session") {
			f.t.Fatal("expected session refresh")
		}
		f.latestAT = "at-" + string(rune('0'+f.step))
		f.latestCookie = "cookie-" + string(rune('0'+f.step))
		response.Content = []byte(`{"accessToken":"` + f.latestAT + `","account":{"id":"acct"}}`)
		response.Headers["Set-Cookie"] = []string{"__Secure-next-auth.session-token=" + f.latestCookie + "; Path=/"}
		f.pending = true
	case 3, 5:
		if !strings.Contains(target, "/accounts/check/") || protocol_AsString(options.Headers.Get("Authorization")) != "Bearer "+f.latestAT {
			f.t.Fatal("account check used old access token")
		}
		response.StatusCode = 401
		if f.step == 5 {
			response.StatusCode = 400
		}
	default:
		f.t.Fatal("unexpected later checkout request")
	}
	return response, nil
}
func (*checkoutContinuityTransport) Pause(float64) error { return nil }
func (*checkoutContinuityTransport) Close()              {}
func (*checkoutContinuityTransport) Deadline() time.Time { return time.Now().Add(time.Minute) }

func TestRealCheckoutFlowSavesBothInitialAnd401RefreshBeforeContinuing(t *testing.T) {
	for _, failSave := range []bool{false, true} {
		tr := &checkoutContinuityTransport{t: t, latestAT: "initial-at", latestCookie: "initial-cookie"}
		client := gptpay_NewPaymentClient(model_DefaultConfig())
		client.transportFactory = func(context.Context, string, float64, time.Time) (protocol_HTTPTransport, error) { return tr, nil }
		var saved []model_Session
		ctx := context.WithValue(context.Background(), checkoutCredentialContextKey{}, checkoutCredentialSink(func(s model_Session) error {
			if failSave {
				return protocol_NewError("checkpoint_failed")
			}
			if s.AccessToken != tr.latestAT || s.SessionToken != tr.latestCookie {
				t.Fatal("checkout saved older credentials")
			}
			saved = append(saved, s)
			tr.pending = false
			return nil
		}))
		result, err := client.CreateCheckout(ctx, model_Session{AccessToken: "initial-at", SessionToken: "initial-cookie", AccountID: "acct", Email: "a@example.test"}, gptpay_CreateCheckoutOptions{Country: "PH", Plan: "plus", CheckoutType: "hosted"})
		if err != nil {
			t.Fatal(err)
		}
		if failSave {
			if result.ErrorCode != "checkpoint_failed" || tr.step != 2 || len(saved) != 0 {
				t.Fatal("checkout did not stop at failed save")
			}
		} else if result.ErrorCode != "http_400" || len(saved) != 2 || saved[1].AccessToken != "at-4" {
			t.Fatal("checkout lost the second refresh on later failure")
		}
	}
}

func TestCheckoutCookieRotationAndDeletion(t *testing.T) {
	base := "__Secure-next-auth.session-token"
	for _, tc := range []struct {
		old     string
		headers []string
		want    string
	}{
		{base + "=old", []string{base + ".1=two", base + ".0=one"}, "onetwo"},
		{base + ".0=old; " + base + ".1=stale", []string{base + "=new"}, "new"},
		{base + ".0=old; " + base + ".1=stale; " + base + ".2=tail", []string{base + ".0=fresh", base + ".1=end"}, "freshend"},
		{base + "=old", []string{base + "=; Max-Age=0"}, ""},
	} {
		raw := checkoutAbsorbCookies(tc.old, &protocol_HTTPResponse{Headers: http.Header{"Set-Cookie": tc.headers}})
		if got := checkoutSessionToken(raw); got != tc.want {
			t.Fatalf("wrong login cookie reconstruction")
		}
	}
}

type credentialCLIClient struct {
	teamCaptureClient
	called bool
}

func (c *credentialCLIClient) CreateCheckout(ctx context.Context, s model_Session, _ gptpay_CreateCheckoutOptions) (model_CheckoutResult, error) {
	c.called = true
	save, ok := ctx.Value(checkoutCredentialContextKey{}).(checkoutCredentialSink)
	if !ok {
		return model_CheckoutResult{}, errors.New("sink missing")
	}
	s.AccessToken = "fresh-private-at"
	if err := save(s); err != nil {
		return model_CheckoutResult{}, err
	}
	return model_CheckoutResult{}, protocol_NewError("http_400")
}

func TestCheckoutCredentialACKProtocolAndPrivateResult(t *testing.T) {
	for _, ack := range []string{"{\"ok\":true}\n", "{\"ok\":false}\n", "bad\n", ""} {
		r := cli_NewRunner()
		r.In = strings.NewReader(`{"accessToken":"private-at","sessionToken":"private-cookie","account":{"id":"acct"},"user":{"email":"a@example.test"}}` + "\n" + ack)
		out, diagnostic := &bytes.Buffer{}, &bytes.Buffer{}
		r.Out, r.Err = out, diagnostic
		client := &credentialCLIClient{}
		r.ClientFactory = func(model_Config, gptpay_ClientOptions) cli_Client { return client }
		if code := r.CreateCheckout(context.Background(), []string{"--country", "PH", "--plan", "plus", "--credential-checkpoints"}); code != 1 || !client.called {
			t.Fatal("CLI bridge not called")
		}
		dec := json.NewDecoder(out)
		var frame, result map[string]any
		if dec.Decode(&frame) != nil || frame["type"] != "credentials" || dec.Decode(&result) != nil {
			t.Fatal("invalid IPC frames")
		}
		want := "checkpoint_failed"
		if strings.Contains(ack, "true") {
			want = "http_400"
		}
		if result["error_code"] != want {
			t.Fatal("continued without successful save acknowledgement")
		}
		b, _ := json.Marshal(result)
		if strings.Contains(string(b), "private") || strings.Contains(diagnostic.String(), "private") {
			t.Fatal("secret leaked to public output")
		}
	}
}
