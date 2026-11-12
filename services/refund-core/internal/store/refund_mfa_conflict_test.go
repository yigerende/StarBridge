package store

import (
	"encoding/json"
	"errors"
	"testing"
)

func TestMFAConflictUsesExistingRecoveryAndWallet(t *testing.T) {
	testLoginConflictRecoveryAndWallet(t, 409, "mfa_verify", "mfa_state_conflict")
}

func TestTeamSessionNotReadyUsesExistingRecoveryAndWallet(t *testing.T) {
	testLoginConflictRecoveryAndWallet(t, 200, "session", "team_session_not_ready")
}

func testLoginConflictRecoveryAndWallet(t *testing.T, status int, stage, reason string) {
	t.Helper()
	for _, succeeds := range []bool{true, false} {
		name := "budget_exhausted"
		if succeeds {
			name = "retry_succeeds"
		}
		t.Run(name, func(t *testing.T) {
			s, admin, user := fixture(t)
			configure(t, s, admin, 1)
			cfg, _ := backupFixture(t, s, admin)
			cfg.RetrySeconds = 17
			cfg.Recovery.Enabled = true
			cfg.Recovery.MaxRetries = 1
			cfg.Recovery.CleanupFailed = true
			cfg.ResourceRecovery.LoginFailureLimit = 2
			if err := s.SaveRefundSettings(testctx, admin, cfg); err != nil {
				t.Fatal(err)
			}
			job := resourceJob(t, s, user, "mfa@fixture.invalid")
			for _, step := range []string{"inviting", "invited", "login"} {
				checkpoint(t, s, job, step)
			}
			diagnostic := RefundDiagnostic{Code: "login_failed", HTTPStatus: status, Retryable: true,
				Evidence: &RefundEvidence{Side: "child", Stage: stage, Reason: reason, Exception: "LoginFlowError"}}
			// A repeated completion report must not schedule or settle twice.
			for i := 0; i < 2; i++ {
				if err := s.FinishRefundAttempt(testctx, job, diagnostic); err != nil {
					t.Fatal(err)
				}
			}
			balance(t, s, user, 10000, 1000)
			var mode string
			var delay float64
			var raw []byte
			if err := s.Pool.QueryRow(testctx, `SELECT recovery_mode,EXTRACT(EPOCH FROM next_attempt_at-updated_at),last_diagnostic
 FROM refund_execution WHERE refund_id=$1`, job.ID).Scan(&mode, &delay, &raw); err != nil {
				t.Fatal(err)
			}
			if mode != "retry" || delay != float64(cfg.RetrySeconds) {
				t.Fatal("existing retry delay not applied", mode, delay)
			}
			var saved RefundDiagnostic
			if err := json.Unmarshal(raw, &saved); err != nil {
				t.Fatal(err)
			}
			if saved.Code != "login_failed" || !saved.Retryable || saved.HTTPStatus != status || saved.Evidence == nil || saved.Evidence.Reason != reason || saved.Evidence.Stage != stage {
				t.Fatal("retry evidence lost", saved)
			}
			if early, err := s.ClaimRefund(testctx); err != nil || early != nil {
				t.Fatal("retried before configured interval", err)
			}
			due(t, s, job.ID)
			retry, err := s.ClaimRefund(testctx)
			if err != nil || retry == nil {
				t.Fatal("missing scheduled retry", err)
			}
			if retry.ID != job.ID || retry.Mother.ID != job.Mother.ID || retry.ChildProxy != job.ChildProxy || retry.MotherProxy != job.MotherProxy || retry.RecoveryMode != "retry" || retry.Step != "login" || retry.DiagnosticContext.Retry != 1 {
				t.Fatal("retry changed original assignment, phase or budget")
			}
			var cooled, healthFaults, switches int
			if err := s.Pool.QueryRow(testctx, "SELECT count(*) FROM refund_proxies WHERE failure_until IS NOT NULL").Scan(&cooled); err != nil {
				t.Fatal(err)
			}
			if err := s.Pool.QueryRow(testctx, "SELECT count(*) FROM refund_resource_health WHERE failures>0 OR blocked").Scan(&healthFaults); err != nil {
				t.Fatal(err)
			}
			if err := s.Pool.QueryRow(testctx, "SELECT child_proxy_switches FROM refund_execution WHERE refund_id=$1", job.ID).Scan(&switches); err != nil {
				t.Fatal(err)
			}
			if cooled != 0 || healthFaults != 0 || switches != 0 {
				t.Fatal("login conflict incorrectly treated as a network outage", cooled, healthFaults, switches)
			}
			finished, completed := retry, "completed"
			if succeeds {
				for _, step := range []string{"merge_v2_ready", "merge_v2_submitting", "merge_v2_accepted", "removing", "completed"} {
					checkpoint(t, s, retry, step)
				}
				balance(t, s, user, 9000, 0)
			} else {
				if err := s.FinishRefundAttempt(testctx, retry, diagnostic); err != nil {
					t.Fatal(err)
				}
				balance(t, s, user, 10000, 1000)
				due(t, s, job.ID)
				cleanup, err := s.ClaimRefund(testctx)
				if err != nil || cleanup == nil || cleanup.RecoveryMode != "cleanup" {
					t.Fatal("retry budget exhaustion did not use existing cleanup", err)
				}
				for _, step := range []string{"cleanup_started", "cleanup_completed"} {
					checkpoint(t, s, cleanup, step)
				}
				balance(t, s, user, 10000, 0)
				finished, completed = cleanup, "cleanup_completed"
			}
			if err := s.RefundCheckpoint(testctx, finished, Checkpoint{Method: completed, TeamID: finished.Mother.TeamID, UserID: "offline-child"}); !errors.Is(err, ErrState) {
				t.Fatal("duplicate completion bypassed existing lease fence", err)
			}
			var freezes, charges, releases, retries int
			if err := s.Pool.QueryRow(testctx, `SELECT count(*) FILTER(WHERE kind='freeze'),count(*) FILTER(WHERE kind='charge'),count(*) FILTER(WHERE kind='unfreeze')
 FROM ledger WHERE business='refund' AND refund_order_id=$1`, job.ID).Scan(&freezes, &charges, &releases); err != nil {
				t.Fatal(err)
			}
			if err := s.Pool.QueryRow(testctx, "SELECT retry_count FROM refund_execution WHERE refund_id=$1", job.ID).Scan(&retries); err != nil {
				t.Fatal(err)
			}
			if freezes != 1 || retries != cfg.Recovery.MaxRetries || (succeeds && (charges != 1 || releases != 0)) || (!succeeds && (charges != 0 || releases != 1)) {
				t.Fatal("duplicate settlement or changed retry budget", freezes, charges, releases, retries)
			}
		})
	}
}
