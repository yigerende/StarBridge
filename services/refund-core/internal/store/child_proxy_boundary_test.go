package store

import (
	"errors"
	"sync"
	"testing"
	"time"

	"llm-refund/internal/refundprovider"
)

func boundaryJob(t *testing.T, s *Store, user string) *RefundJob {
	t.Helper()
	if _, _, err := s.CreateRefunds(testctx, user, "boundary", []refundprovider.Account{account("boundary@fixture.invalid")}, 1000); err != nil {
		t.Fatal(err)
	}
	j, err := s.ClaimRefund(testctx)
	if err != nil || j == nil {
		t.Fatal("claim", err)
	}
	for _, step := range []string{"inviting", "invited", "login"} {
		checkpoint(t, s, j, step)
	}
	return j
}

func signalChildFailure(s *Store, j *RefundJob) error {
	d := childNetwork()
	return s.RefundCheckpoint(testctx, j, Checkpoint{Method: "child_proxy_network_failure", Diagnostic: &d})
}

func cooldown(t *testing.T, s *Store, id string) time.Time {
	t.Helper()
	var until time.Time
	if err := s.Pool.QueryRow(testctx, "SELECT failure_until FROM refund_proxies WHERE id=$1", id).Scan(&until); err != nil {
		t.Fatal(err)
	}
	return until
}

func TestChildBoundaryPersistsImmediatelyAndDuplicatesDoNotExtendCooldown(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 2)
	cfg, backup := backupFixture(t, s, a)
	j := boundaryJob(t, s, u)
	// Settings may change while a worker is already running. Use their value
	// at failure time without changing the worker's other scheduling snapshot.
	cfg.ChildProxyCooldownSeconds = 600
	if err := s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	if err := signalChildFailure(s, j); err != nil {
		t.Fatal(err)
	}
	until := cooldown(t, s, cfg.ChildProxyID)
	if remaining := time.Until(until); remaining < 590*time.Second || remaining > 601*time.Second {
		t.Fatal("cooldown was not immediate", remaining)
	}
	var pending bool
	var step, token string
	var retries int
	if err := s.Pool.QueryRow(testctx, "SELECT child_proxy_switch_pending,step,lease_token,retry_count FROM refund_execution WHERE refund_id=$1", j.ID).Scan(&pending, &step, &token, &retries); err != nil {
		t.Fatal(err)
	}
	if !pending || step != "login" || token != j.LeaseToken || retries != 0 {
		t.Fatal("signal changed execution rather than only cooling it")
	}
	balance(t, s, u, 10000, 1000)
	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := signalChildFailure(s, j); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	if !cooldown(t, s, cfg.ChildProxyID).Equal(until) {
		t.Fatal("duplicate signal extended cooldown")
	}
	// A different order must avoid the bad exit before the first worker ends.
	if _, _, err := s.CreateRefunds(testctx, u, "parallel-boundary", []refundprovider.Account{account("parallel@fixture.invalid")}, 1000); err != nil {
		t.Fatal(err)
	}
	other, err := s.ClaimRefund(testctx)
	if err != nil || other == nil || other.DiagnosticContext.ChildProxyID != backup {
		t.Fatal("bad exit assigned during current attempt", err)
	}
	if err = s.FinishRefundAttempt(testctx, j, childNetwork()); err != nil {
		t.Fatal(err)
	}
	if !cooldown(t, s, cfg.ChildProxyID).Equal(until) {
		t.Fatal("final result extended checkpoint cooldown")
	}
	due(t, s, j.ID)
	retry, err := s.ClaimRefund(testctx)
	if err != nil || retry == nil || retry.ID != j.ID || retry.Step != "login" || retry.DiagnosticContext.ChildProxyID != backup || retry.MotherProxy != j.MotherProxy || retry.Mother.ID != j.Mother.ID {
		t.Fatal("original order did not resume on backup", err)
	}
	// Old worker signals cannot cool the new binding.
	if err = signalChildFailure(s, j); !errors.Is(err, ErrState) {
		t.Fatal("old lease accepted", err)
	}
	var backupCooling bool
	if err = s.Pool.QueryRow(testctx, "SELECT failure_until IS NOT NULL FROM refund_proxies WHERE id=$1", backup).Scan(&backupCooling); err != nil || backupCooling {
		t.Fatal("stale signal cooled backup", err)
	}
	for _, step := range []string{"merge_v2_ready", "merge_v2_submitting", "merge_v2_accepted", "removing", "completed"} {
		checkpoint(t, s, retry, step)
	}
	balance(t, s, u, 9000, 1000)
	if !cooldown(t, s, cfg.ChildProxyID).Equal(until) {
		t.Fatal("backup success cleared primary cooldown")
	}
}

func TestChildBoundarySurvivesCrashWithoutLosingSubmissionFence(t *testing.T) {
	for _, submitted := range []bool{false, true} {
		t.Run(map[bool]string{false: "login", true: "submitted"}[submitted], func(t *testing.T) {
			s, a, u := fixture(t)
			configure(t, s, a, 1)
			_, backup := backupFixture(t, s, a)
			j := boundaryJob(t, s, u)
			if submitted {
				checkpoint(t, s, j, "merge_v2_ready")
				checkpoint(t, s, j, "merge_v2_submitting")
			}
			if err := signalChildFailure(s, j); err != nil {
				t.Fatal(err)
			}
			// Simulate process loss after durable checkpoint and before result.
			if _, err := s.Pool.Exec(testctx, "UPDATE refund_execution SET lease_until=now()-interval '1 second' WHERE refund_id=$1", j.ID); err != nil {
				t.Fatal(err)
			}
			if abandoned, err := s.ClaimRefund(testctx); err != nil || abandoned != nil {
				t.Fatal("interrupted attempt bypassed recovery scheduling", err)
			}
			due(t, s, j.ID)
			retry, err := s.ClaimRefund(testctx)
			if err != nil || retry == nil || retry.ID != j.ID || retry.DiagnosticContext.ChildProxyID != backup {
				t.Fatal("lost durable backup switch", err)
			}
			if submitted {
				if retry.Step != "submitting" || retry.RecoveryMode != "fallback" {
					t.Fatal("unknown export lost fence")
				}
				if err = s.RefundCheckpoint(testctx, retry, Checkpoint{Method: "merge_v2_submitting", TeamID: retry.Mother.TeamID}); !errors.Is(err, ErrState) {
					t.Fatal("unknown export was resubmitted", err)
				}
			} else if retry.Step != "login" || retry.RecoveryMode != "retry" {
				t.Fatal("login did not resume original stage")
			}
			balance(t, s, u, 10000, 1000)
		})
	}
}

func TestChildBoundaryRejectsNonNetworkAndMotherSignals(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	j := boundaryJob(t, s, u)
	for _, d := range []RefundDiagnostic{
		{Code: "mother_network", NetworkCode: 28, Evidence: &RefundEvidence{Side: "mother", Reason: "network_error"}},
		{Code: "login_auth_failed", HTTPStatus: 401, Evidence: &RefundEvidence{Side: "child", Reason: "login_auth_failed"}},
		{Code: "login_failed", HTTPStatus: 429, Evidence: &RefundEvidence{Side: "child", Reason: "chatgpt_rate_limited"}},
	} {
		if err := s.RefundCheckpoint(testctx, j, Checkpoint{Method: "child_proxy_network_failure", Diagnostic: &d}); !errors.Is(err, ErrState) {
			t.Fatal("non-child-network evidence accepted", err)
		}
	}
	var cooling int
	if err := s.Pool.QueryRow(testctx, "SELECT count(*) FROM refund_proxies WHERE failure_until IS NOT NULL").Scan(&cooling); err != nil || cooling != 0 {
		t.Fatal("unrelated proxy cooled", err)
	}
	balance(t, s, u, 10000, 1000)
}
