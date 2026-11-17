package main

import (
	"fmt"
	"strings"
	"testing"
)

type rotatingSessionTransport struct {
	*fixtureTransport
	count int
}

func (f *rotatingSessionTransport) Request(method, target string, opts protocol_RequestOptions) (*protocol_HTTPResponse, error) {
	if f.count > 0 && sessionFromCookies(protocol_AsString(opts.Headers.Get("Cookie"))) != fmt.Sprintf("fixture-cookie-%d", f.count) {
		f.t.Fatal("request did not use latest login cookie")
	}
	response, err := f.fixtureTransport.Request(method, target, opts)
	if err != nil {
		return response, err
	}
	f.count++
	// Alternate chunked and unchunked tokens so stale names cannot mask rotation.
	if f.count%2 == 1 {
		response.Headers["Set-Cookie"] = []string{"__Secure-next-auth.session-token.0=fixture-cookie-; Path=/", fmt.Sprintf("__Secure-next-auth.session-token.1=%d; Path=/", f.count)}
	} else {
		response.Headers["Set-Cookie"] = []string{fmt.Sprintf("__Secure-next-auth.session-token=fixture-cookie-%d; Path=/", f.count)}
	}
	return response, nil
}

func TestWholePaidFlowAlwaysUsesLatestSession(t *testing.T) {
	tx, fixture := fixture(t, false,
		step{"/checkout/verify", 200, "<html>return</html>", ""},
		step{"subscriptions", 401, `{}`, ""},
		step{"/api/auth/session", 200, refreshed, ""},
		step{"subscriptions", 200, active, ""},
		step{"/api/auth/session", 200, refreshed, ""},
		step{"subscriptions", 401, `{}`, ""},
		step{"/api/auth/session", 200, refreshed, ""},
		step{"subscriptions", 200, active, ""},
		step{"/subscriptions/cancel", 401, `{}`, ""},
		step{"/api/auth/session", 200, refreshed, ""},
		step{"subscriptions", 200, active, ""},
		step{"/subscriptions/cancel", 200, `{}`, ""},
		step{"subscriptions", 200, off, ""},
	)
	transport := &rotatingSessionTransport{fixtureTransport: fixture}
	tx.http, tx.platform.HTTP = transport, transport
	tx.cp.ReturnURL = "https://chatgpt.com/checkout/verify?stripe_session_id=cs_fixture"
	saves := 0
	tx.saveFn = func(cp *model_Checkpoint, phase string) error {
		if transport.count > 0 && cp.SessionToken != fmt.Sprintf("fixture-cookie-%d", transport.count) {
			t.Fatal("checkpoint contains stale session token")
		}
		saves++
		return nil
	}
	if err := tx.finishPayment(); err != nil {
		t.Fatal(err)
	}
	if len(fixture.steps) != 0 || tx.cp.Phase != "completed" || saves < 6 {
		t.Fatal("flow did not finish and save all refreshes")
	}
	copy := *tx.cp
	state := state{Executor: executorVersion, Context: &copy, AccountID: copy.AccountID, CheckoutID: copy.CheckoutID,
		Plan: copy.Plan, CancelRequested: copy.CancelRequested, Credentials: credentials{AccessToken: copy.AccessToken, AccountID: copy.AccountID, SessionToken: copy.SessionToken}}
	recovered, err := initialCheckpoint(input{Operation: "finish", State: state})
	if err != nil || recovered.SessionToken != tx.cp.SessionToken || recovered.AccessToken != tx.cp.AccessToken {
		t.Fatal("recovery lost latest credentials")
	}
}

func TestRefreshedCredentialsWinOverOldRequestSnapshot(t *testing.T) {
	in := input{Operation: "refresh", State: state{Executor: executorVersion, AccountID: "acct", Credentials: credentials{
		AccessToken: "new-at", SessionToken: "new-cookie", AccountID: "acct", RefreshedAt: "2026-11-17T00:00:00Z",
		Cookies: map[string]string{"__Secure-next-auth.session-token": "new-cookie"}}}}
	in.State.RequestContext.Cookies = []cookieSnapshot{{Name: "__Secure-next-auth.session-token.0", Value: "stale", Domain: "chatgpt.com", Path: "/"}, {Name: "oai-did", Value: "device", Domain: "chatgpt.com", Path: "/"}}
	cp, err := initialCheckpoint(in)
	if err != nil || cp.SessionToken != "new-cookie" || strings.Contains(cp.Cookies, "stale") || cp.DeviceID != "device" {
		t.Fatal("old checkout snapshot overwrote refreshed login")
	}
}

func TestRefreshRetryReusesNewCookieWithoutTokenRollback(t *testing.T) {
	tx, fixture := fixture(t, true)
	fixture.steps = []step{{"/api/auth/session", 200, `{"error":"RefreshAccessTokenError"}`, ""}, {"/api/auth/session", 200, refreshed, ""}}
	transport := &rotatingSessionTransport{fixtureTransport: fixture}
	tx.platform.HTTP = transport
	if err := tx.platform.Refresh(); err != nil {
		t.Fatal(err)
	}
	if tx.cp.AccessToken != "new-access" || tx.cp.SessionToken != "fixture-cookie-2" {
		t.Fatal("retry did not retain refreshed credentials")
	}
}

func TestMismatchedRefreshCannotReplaceSavedCookie(t *testing.T) {
	for _, body := range []string{`{"accessToken":"other","account":{"id":"other"}}`, `{"accessToken":"other","user":{"email":"other@example.test"}}`} {
		tx, transport := fixture(t, true)
		tx.cp.Email = "owner@example.test"
		beforeCookie, beforeSession := tx.cp.Cookies, tx.cp.SessionToken
		transport.steps = []step{{"/api/auth/session", 200, body, ""}}
		if err := tx.platform.Refresh(); protocol_FlowErrorCode(err) != "account_mismatch" {
			t.Fatal("identity mismatch accepted")
		}
		if tx.cp.Cookies != beforeCookie || tx.cp.SessionToken != beforeSession || tx.cp.AccessToken != "old-access" {
			t.Fatal("mismatched login state retained")
		}
	}
}

func TestOrderCredentialsReplaceStaleRecoveryAuthOnly(t *testing.T) {
	cp := &model_Checkpoint{AccountID: "acct", AccessToken: "stale-at", SessionToken: "stale-session", Cookies: "__Secure-next-auth.session-token.0=stale; oai-did=device", Phase: "cancellation_requested", CancelAttempts: 1, Metadata: protocol_Map{}}
	in := input{Operation: "refresh", State: state{Executor: executorVersion, AccountID: "acct", Context: cp,
		Credentials: credentials{AccountID: "acct", AccessToken: "latest-at", SessionToken: "latest-session", Cookies: map[string]string{"__Secure-next-auth.session-token": "latest-session"}}}}
	got, err := initialCheckpoint(in)
	if err != nil || got.AccessToken != "latest-at" || got.SessionToken != "latest-session" || strings.Contains(got.Cookies, "stale") {
		t.Fatal("recovery used stale snapshot credentials")
	}
	if got.Phase != "cancellation_requested" || got.CancelAttempts != 1 || !strings.Contains(got.Cookies, "oai-did=device") {
		t.Fatal("credential replacement changed saved execution progress")
	}
}
