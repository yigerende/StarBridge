package main

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

// Assert both the credential version and durable-save boundary at EVERY request.
type continuityTransport struct {
	*fixtureTransport
	at, cookie  string
	refreshes   int
	pendingSave bool
}

func (f *continuityTransport) Request(method, target string, options protocol_RequestOptions) (*protocol_HTTPResponse, error) {
	if f.pendingSave {
		f.t.Fatal("continued upstream work before refreshed credentials were saved")
	}
	if sessionFromCookies(protocol_AsString(options.Headers.Get("Cookie"))) != f.cookie {
		f.t.Fatal("request reverted to an old session")
	}
	isRefresh := strings.Contains(target, "/api/auth/session")
	if !isRefresh && protocol_AsString(options.Headers.Get("Authorization")) != "Bearer "+f.at {
		f.t.Fatal("request reverted to an old AT")
	}
	response, err := f.fixtureTransport.Request(method, target, options)
	if err != nil {
		return response, err
	}
	if isRefresh && response.StatusCode == 200 {
		f.refreshes++
		f.at = fmt.Sprintf("fresh-at-%d", f.refreshes)
		f.cookie = fmt.Sprintf("fresh-cookie-%d", f.refreshes)
		response.Content = []byte(fmt.Sprintf(`{"accessToken":%q,"account":{"id":"fixture-account"}}`, f.at))
		response.Headers["Set-Cookie"] = []string{"__Secure-next-auth.session-token=" + f.cookie + "; Path=/"}
		f.pendingSave = true
	}
	return response, nil
}

func attachContinuity(t *testing.T, tx *gptpay_transaction, f *fixtureTransport) (*continuityTransport, **model_Checkpoint) {
	transport := &continuityTransport{fixtureTransport: f, at: tx.cp.AccessToken, cookie: tx.cp.SessionToken}
	tx.http, tx.platform.HTTP = transport, transport
	var saved *model_Checkpoint
	tx.saveFn = func(cp *model_Checkpoint, _ string) error {
		if cp.AccessToken != transport.at || cp.SessionToken != transport.cookie {
			t.Fatal("saved checkpoint has an older credential version")
		}
		b, _ := json.Marshal(cp)
		if err := json.Unmarshal(b, &saved); err != nil {
			t.Fatal(err)
		}
		transport.pendingSave = false
		return nil
	}
	return transport, &saved
}

func TestEveryPaidStageFailureResumesWithNewestSavedSession(t *testing.T) {
	read := step{"subscriptions", 200, active, ""}
	refresh := step{"/api/auth/session", 200, refreshed, ""}
	unauthorized := step{"subscriptions", 401, `{}`, ""}
	for _, tc := range []struct {
		name        string
		steps       []step
		landing     bool
		failInitial bool
	}{
		{"initial_refresh", nil, false, true},
		{"merchant_return", []step{{"/checkout/verify", 0, "", "network_error"}}, true, false},
		{"activation_read", []step{{"subscriptions", 0, "", "network_error"}}, false, false},
		{"activation_401_refresh", []step{unauthorized, {"/api/auth/session", 0, "", "network_error"}}, false, false},
		{"renewal_proactive_refresh", []step{read, {"/api/auth/session", 0, "", "network_error"}}, false, false},
		{"renewal_read", []step{read, refresh, {"subscriptions", 0, "", "network_error"}}, false, false},
		{"renewal_401_refresh", []step{read, refresh, unauthorized, {"/api/auth/session", 0, "", "network_error"}}, false, false},
		{"cancel_and_verification", []step{read, refresh, read, {"subscriptions/cancel", 0, "", "network_error"}, {"subscriptions", 0, "", "network_error"}}, false, false},
		{"cancel_401_refresh", []step{read, refresh, read, {"subscriptions/cancel", 401, `{}`, ""}, {"/api/auth/session", 0, "", "network_error"}}, false, false},
		{"final_verification", []step{read, refresh, read, {"subscriptions/cancel", 200, `{}`, ""}, {"subscriptions", 0, "", "network_error"}}, false, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tx, f := fixture(t, false, tc.steps...)
			if tc.failInitial {
				f.steps = []step{{"/api/auth/session", 0, "", "network_error"}}
			}
			if tc.landing {
				tx.cp.ReturnURL = "https://chatgpt.com/checkout/verify?stripe_session_id=cs_fixture"
			}
			spy, saved := attachContinuity(t, tx, f)
			if code := protocol_FlowErrorCode(tx.finishPayment()); code != "network_error" {
				t.Fatalf("wrong failure: %s", code)
			}
			if *saved == nil || spy.pendingSave || (*saved).AccessToken != spy.at || (*saved).SessionToken != spy.cookie {
				t.Fatal("failed stage lost preceding successful refresh")
			}
			cp := *saved
			resumed, rf := fixture(t, false, step{"subscriptions", 200, off, ""}, refresh, step{"subscriptions", 200, off, ""})
			// Preserve progress, but supply canonical order credentials separately.
			state := state{Executor: executorVersion, Context: cp, AccountID: cp.AccountID, CheckoutID: cp.CheckoutID, Plan: cp.Plan,
				CancelRequested: cp.CancelRequested, Credentials: credentials{AccountID: cp.AccountID, AccessToken: spy.at, SessionToken: spy.cookie}}
			latest, err := initialCheckpoint(input{Operation: "finish", State: state})
			if err != nil {
				t.Fatal(err)
			}
			resumed.cp, resumed.platform.CP = latest, latest
			resumed.skipMerchantReturn = true
			attempts := latest.CancelAttempts
			attachContinuity(t, resumed, rf)
			if err := resumed.finishPayment(); err != nil {
				t.Fatal(err)
			}
			if resumed.cp.Phase != "completed" || resumed.cp.CancelAttempts != attempts || len(rf.steps) != 0 {
				t.Fatal("resume replayed cancellation or lost progress")
			}
		})
	}
}

func TestEveryRefreshSaveFailureStopsBeforeNextUpstreamStep(t *testing.T) {
	for failAt := 1; failAt <= 5; failAt++ {
		t.Run(fmt.Sprint(failAt), func(t *testing.T) {
			tx, f := fixture(t, false,
				step{"subscriptions", 401, `{}`, ""}, step{"/api/auth/session", 200, refreshed, ""}, step{"subscriptions", 200, active, ""},
				step{"/api/auth/session", 200, refreshed, ""}, step{"subscriptions", 401, `{}`, ""}, step{"/api/auth/session", 200, refreshed, ""}, step{"subscriptions", 200, active, ""},
				step{"subscriptions/cancel", 401, `{}`, ""}, step{"/api/auth/session", 200, refreshed, ""}, step{"subscriptions", 200, off, ""})
			spy, _ := attachContinuity(t, tx, f)
			save := tx.saveFn
			tx.saveFn = func(cp *model_Checkpoint, phase string) error {
				if spy.pendingSave && spy.refreshes == failAt {
					return protocol_NewError("checkpoint_failed")
				}
				return save(cp, phase)
			}
			if code := protocol_FlowErrorCode(tx.finishPayment()); code != "checkpoint_failed" || spy.refreshes != failAt {
				t.Fatal("unsaved refreshed login was used after persistence failure")
			}
		})
	}
}
