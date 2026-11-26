package store

import (
	"strings"
	"sync"
	"testing"
)

func loginSession403() RefundDiagnostic {
	return RefundDiagnostic{Code: "login_proxy_session_failed", HTTPStatus: 403, Retryable: true,
		Evidence: &RefundEvidence{Side: "child", Stage: "authorize", Reason: "unknown_error", Exception: "LoginFlowError", BodyKind: "html", Challenge: true}}
}

func loginSessionFixture(t *testing.T, vendor string) (*Store, string, string, *RefundJob) {
	t.Helper()
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	cfg, _ := s.RefundSettings(testctx)
	cfg.ChildProxyID = dynamicChild(t, s, a, vendor, "primary")
	cfg.ChildProxyBackups = []string{dynamicChild(t, s, a, "711proxy", "backup")}
	cfg.Recovery.MaxRetries = 2
	if err := s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	j := resourceJob(t, s, u, "session@fixture.invalid")
	for _, step := range []string{"inviting", "invited", "login"} {
		checkpoint(t, s, j, step)
	}
	return s, a, u, j
}

func sessionCount(t *testing.T, s *Store, id string) int {
	t.Helper()
	var n int
	if err := s.Pool.QueryRow(testctx, "SELECT count(*) FROM refund_child_proxy_sessions WHERE refund_id=$1", id).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func TestLogin403RenewalKeepsProviderBudgetDelayAndBinding(t *testing.T) {
	for _, vendor := range []string{"iproyal", "cliproxy", "1024proxy", "711proxy"} {
		t.Run(vendor, func(t *testing.T) {
			s, _, u, j := loginSessionFixture(t, vendor)
			proxy, mother := j.DiagnosticContext.ChildProxyID, j.Mother.ID
			for attempt := 0; attempt < 2; attempt++ {
				previous := j.ChildProxy
				if err := s.FinishRefundAttempt(testctx, j, loginSession403()); err != nil {
					t.Fatal(err)
				}
				var delay float64
				var cooled, switching bool
				var switches int
				if err := s.Pool.QueryRow(testctx, `SELECT extract(epoch FROM next_attempt_at-now()),child_proxy_switch_pending,child_proxy_switches,
 EXISTS(SELECT 1 FROM refund_proxies WHERE failure_at IS NOT NULL) FROM refund_execution WHERE refund_id=$1`, j.ID).Scan(&delay, &switching, &switches, &cooled); err != nil {
					t.Fatal(err)
				}
				if delay < 4 || delay > 5 || switching || switches != 0 || cooled {
					t.Fatal("retry policy changed", delay, switching, switches, cooled)
				}
				if early, err := s.ClaimRefund(testctx); err != nil || early != nil {
					t.Fatal("interval bypassed", err)
				}
				due(t, s, j.ID)
				next, err := s.ClaimRefund(testctx)
				if err != nil || next == nil {
					t.Fatal("retry unavailable", err)
				}
				if next.ChildProxy == previous || next.DiagnosticContext.ChildProxyID != proxy || next.Mother.ID != mother || next.DiagnosticContext.Retry != attempt+1 {
					t.Fatal("session or retry mismatch")
				}
				j = next
				balance(t, s, u, 10000, 1000)
			}
			// Exhaustion schedules cleanup, without generating a fourth binding.
			if err := s.FinishRefundAttempt(testctx, j, loginSession403()); err != nil {
				t.Fatal(err)
			}
			if n := sessionCount(t, s, j.ID); n != 3 {
				t.Fatal("extra session allocated", n)
			}
		})
	}
}

func TestLogin403ConcurrentFinishAndRestartOnlyRenewOnce(t *testing.T) {
	s, a, _, j := loginSessionFixture(t, "iproyal")
	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
		wg.Add(1)
		copyJob := *j
		go func() {
			defer wg.Done()
			if err := s.FinishRefundAttempt(testctx, &copyJob, loginSession403()); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	if sessionCount(t, s, j.ID) != 2 {
		t.Fatal("duplicate terminal report renewed twice")
	}
	// Editing the configured route must not change the newly saved order route.
	if _, err := s.SaveRefundProxy(testctx, a, RefundProxy{ID: j.DiagnosticContext.ChildProxyID, Name: "edited", Vendor: "iproyal", Country: "US", SessionMinutes: 30, Target: "child", Enabled: true}); err != nil {
		t.Fatal(err)
	}
	due(t, s, j.ID)
	next, err := s.ClaimRefund(testctx)
	if err != nil || next == nil || !strings.Contains(next.ChildProxy, "_country-ph_") || !strings.Contains(next.ChildProxy, "_lifetime-1h") {
		t.Fatal("saved binding lost", err)
	}
	endpoint := next.ChildProxy
	resourceSQL(t, s, "UPDATE refund_execution SET lease_until=now()-interval '1 second' WHERE refund_id=$1", j.ID)
	if resumed, err := s.ClaimRefund(testctx); err != nil || resumed != nil {
		t.Fatal("interruption did not schedule", err)
	}
	due(t, s, j.ID)
	resumed, err := s.ClaimRefund(testctx)
	if err != nil || resumed == nil || resumed.ChildProxy != endpoint || sessionCount(t, s, j.ID) != 2 {
		t.Fatal("restart lost or renewed session", err)
	}
}

func TestLocalLoginRenewalCheckpointIsFencedIdempotentAndPersistent(t *testing.T) {
	s, _, _, j := loginSessionFixture(t, "711proxy")
	previous := j.ChildProxySessionRef
	d := loginSession403()
	c := Checkpoint{Method: "child_login_session_retry", Diagnostic: &d, ChildProxyPreviousRef: previous, ChildProxyNextRef: "87654321"}
	for i := 0; i < 2; i++ {
		if err := s.RefundCheckpoint(testctx, j, c); err != nil {
			t.Fatal(err)
		}
	}
	if j.ChildProxySessionRef != "87654321" || sessionCount(t, s, j.ID) != 2 {
		t.Fatal("checkpoint was not idempotent")
	}
	resourceSQL(t, s, "UPDATE refund_execution SET lease_token='' WHERE refund_id=$1", j.ID)
	c.ChildProxyNextRef = "23456789"
	if err := s.RefundCheckpoint(testctx, j, c); err != ErrState {
		t.Fatal("stale worker accepted", err)
	}
	if sessionCount(t, s, j.ID) != 2 {
		t.Fatal("stale worker changed session")
	}
}

func TestLoginSession403RequiresExactLoginFailure(t *testing.T) {
	for _, step := range []string{"submitting", "accepted", "removing", "ready", "inviting"} {
		if childLoginSessionFailure(step, loginSession403()) {
			t.Fatal("other phase accepted", step)
		}
	}
	for _, change := range []func(*RefundDiagnostic){
		func(d *RefundDiagnostic) { d.Code = "account_unavailable" },
		func(d *RefundDiagnostic) { d.HTTPStatus = 401 },
		func(d *RefundDiagnostic) { d.Retryable = false },
		func(d *RefundDiagnostic) { d.Evidence.Side = "mother" },
		func(d *RefundDiagnostic) { d.Evidence.Stage = "export" },
		func(d *RefundDiagnostic) { d.Evidence.Stage = "fallback_plan" },
	} {
		d := loginSession403()
		change(&d)
		if childLoginSessionFailure("login", d) {
			t.Fatal("unrelated failure accepted", d)
		}
	}
}

func TestLogin403ResponseMarkersDoNotVetoPermittedRetry(t *testing.T) {
	for _, challenge := range []bool{false, true} {
		for _, reason := range []string{"unknown_error", "network_error", "chatgpt_signin_failed", "passkey_or_challenge", "login_auth_failed"} {
			d := loginSession403()
			d.Evidence.Challenge, d.Evidence.Reason = challenge, reason
			if !childLoginSessionFailure("login", d) {
				t.Fatal("permitted login 403 retry rejected", challenge, reason)
			}
		}
	}
}

func TestLoginSessionRenewalIsNotReusedForUnknownSubmission(t *testing.T) {
	s, _, u, j := loginSessionFixture(t, "iproyal")
	for _, step := range []string{"merge_v2_ready", "merge_v2_submitting"} {
		checkpoint(t, s, j, step)
	}
	if err := s.FinishRefundAttempt(testctx, j, loginSession403()); err != nil {
		t.Fatal(err)
	}
	var mode string
	if err := s.Pool.QueryRow(testctx, "SELECT recovery_mode FROM refund_execution WHERE refund_id=$1", j.ID).Scan(&mode); err != nil {
		t.Fatal(err)
	}
	if mode == "retry" || sessionCount(t, s, j.ID) != 1 {
		t.Fatal("submission replay enabled", mode)
	}
	balance(t, s, u, 10000, 1000)
}

func TestRenewedLoginSessionSurvivesOrdinaryRetryAndFallback(t *testing.T) {
	s, _, u, j := loginSessionFixture(t, "iproyal")
	if err := s.FinishRefundAttempt(testctx, j, loginSession403()); err != nil {
		t.Fatal(err)
	}
	due(t, s, j.ID)
	j, err := s.ClaimRefund(testctx)
	if err != nil || j == nil {
		t.Fatal(err)
	}
	endpoint := j.ChildProxy
	if err = s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "login_response_incomplete", Retryable: true}); err != nil {
		t.Fatal(err)
	}
	due(t, s, j.ID)
	j, err = s.ClaimRefund(testctx)
	if err != nil || j == nil || j.ChildProxy != endpoint {
		t.Fatal("ordinary retry lost renewed session", err)
	}
	for _, step := range []string{"merge_v2_ready", "merge_v2_submitting"} {
		checkpoint(t, s, j, step)
	}
	if err = s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "export_uncertain", HTTPStatus: 500}); err != nil {
		t.Fatal(err)
	}
	due(t, s, j.ID)
	j, err = s.ClaimRefund(testctx)
	if err != nil || j == nil || j.RecoveryMode != "fallback" || j.ChildProxy != endpoint {
		t.Fatal("fallback lost renewed session", err)
	}
	for _, step := range []string{"fallback_removing", "fallback_removed", "fallback_login", "fallback_plan_started", "fallback_free"} {
		checkpoint(t, s, j, step)
	}
	balance(t, s, u, 9000, 0)
	if sessionCount(t, s, j.ID) != 2 {
		t.Fatal("later stage renewed session unexpectedly")
	}
}

func TestLogin403DoesNotRenewWhenRecoveryPausedOrProxyStatic(t *testing.T) {
	for _, vendor := range []string{"iproyal", "other"} {
		t.Run(vendor, func(t *testing.T) {
			s, _, _, j := loginSessionFixture(t, vendor)
			if vendor == "iproyal" {
				resourceSQL(t, s, `UPDATE refund_settings SET recovery=jsonb_set(recovery,'{enabled}','false')`)
			}
			if err := s.FinishRefundAttempt(testctx, j, loginSession403()); err != nil {
				t.Fatal(err)
			}
			if sessionCount(t, s, j.ID) != 1 {
				t.Fatal("unexpected renewal")
			}
		})
	}
}
