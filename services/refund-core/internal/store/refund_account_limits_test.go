package store

import (
	"errors"
	"fmt"
	"os"
	"sync"
	"sync/atomic"
	"testing"

	"llm-refund/internal/refundprovider"
)

func termination() RefundDiagnostic {
	return RefundDiagnostic{Code: "account_unavailable", HTTPStatus: 403, Evidence: &RefundEvidence{Side: "child", Stage: "mfa_verify", Reason: "account_deactivated", UpstreamCode: "account_deactivated"}}
}
func recordTermination(t *testing.T, s *Store, id string, d RefundDiagnostic) {
	t.Helper()
	tx, e := s.Pool.Begin(testctx)
	if e != nil {
		t.Fatal(e)
	}
	defer tx.Rollback(testctx)
	if e = recordDiagnostic(testctx, tx, id, d); e != nil {
		t.Fatal(e)
	}
	if e = tx.Commit(testctx); e != nil {
		t.Fatal(e)
	}
}
func restrictedAccount(t *testing.T, s *Store, u, email string) string {
	t.Helper()
	ids, _, e := s.CreateRefunds(testctx, u, ID(), []refundprovider.Account{account(email)}, 1000)
	if e != nil {
		t.Fatal(e)
	}
	recordTermination(t, s, ids[0], termination())
	if e = s.Transition(testctx, u, ids[0], "cancelled", "test"); e != nil {
		t.Fatal(e)
	}
	return ids[0]
}
func TestAccountRestrictionCountsNewOrdersOnly(t *testing.T) {
	s, admin, u := fixture(t)
	cfg, _ := s.RefundSettings(testctx)
	n := 2
	cfg.DeactivatedResubmitLimit = &n
	if e := s.SaveRefundSettings(testctx, admin, cfg); e != nil {
		t.Fatal(e)
	}
	original := restrictedAccount(t, s, u, "limit@test.invalid")
	for i := 0; i < 2; i++ {
		key := ID()
		ids, replay, e := s.CreateRefunds(testctx, u, key, []refundprovider.Account{account("limit@test.invalid")}, 1000)
		if e != nil || replay {
			t.Fatalf("new: %v %v", replay, e)
		}
		// A failed mixed batch must consume neither funds nor retry allowance.
		if i == 0 {
			before := 0
			s.Pool.QueryRow(testctx, "SELECT count(*) FROM refunds").Scan(&before)
			_, _, e = s.CreateRefunds(testctx, u, ID(), []refundprovider.Account{account("rollback@test.invalid"), account("limit@test.invalid")}, 1000)
			if !errors.Is(e, ErrDuplicate) {
				t.Fatal(e)
			}
			after := 0
			s.Pool.QueryRow(testctx, "SELECT count(*) FROM refunds").Scan(&after)
			if before != after {
				t.Fatal("partial batch persisted")
			}
		}
		recordTermination(t, s, ids[0], termination())
		recordTermination(t, s, ids[0], termination()) // Internal repeats never increment/reset.
		if e = s.Transition(testctx, u, ids[0], "cancelled", "test"); e != nil {
			t.Fatal(e)
		}
		got, replay, e := s.CreateRefunds(testctx, u, key, []refundprovider.Account{account("limit@test.invalid")}, 1000)
		if e != nil || !replay || got[0] != ids[0] {
			t.Fatalf("replay: %v %v", replay, e)
		}
	}
	for _, owner := range []string{u, admin} {
		_, _, e := s.CreateRefunds(testctx, owner, ID(), []refundprovider.Account{account("LIMIT@test.invalid")}, 1000)
		if !errors.Is(e, ErrAccountRestricted) {
			t.Fatalf("global restriction: %v", e)
		}
	}
	var used, frozen int
	s.Pool.QueryRow(testctx, "SELECT resubmissions FROM refund_account_restrictions").Scan(&used)
	s.Pool.QueryRow(testctx, "SELECT frozen FROM users WHERE id=$1", u).Scan(&frozen)
	if used != 2 || frozen != 0 {
		t.Fatalf("used=%d frozen=%d", used, frozen)
	}
	// Release is durable across startup and late results from earlier orders.
	s.Pool.Exec(testctx, "UPDATE refund_account_restrictions SET cleared_at=clock_timestamp(),resubmissions=0")
	recordTermination(t, s, original, termination())
	raw, _ := os.ReadFile("../../../../backend/refund_schema.sql")
	if _, e := s.Pool.Exec(testctx, string(raw)); e != nil {
		t.Fatal(e)
	}
	var cleared bool
	s.Pool.QueryRow(testctx, "SELECT cleared_at IS NOT NULL FROM refund_account_restrictions").Scan(&cleared)
	if !cleared {
		t.Fatal("old evidence reinstated a cleared account")
	}
	ids, _, e := s.CreateRefunds(testctx, u, ID(), []refundprovider.Account{account("limit@test.invalid")}, 1000)
	if e != nil {
		t.Fatal(e)
	}
	recordTermination(t, s, ids[0], termination())
	s.Pool.QueryRow(testctx, "SELECT cleared_at IS NOT NULL,resubmissions FROM refund_account_restrictions").Scan(&cleared, &used)
	if cleared || used != 0 {
		t.Fatal("new confirmed failure must start a fresh allowance")
	}
}

func TestAccountRestrictionConcurrentAdmissionAndAtomicRollback(t *testing.T) {
	s, _, u := fixture(t)
	restrictedAccount(t, s, u, "concurrent@test.invalid")
	s.Pool.Exec(testctx, "UPDATE refund_settings SET deactivated_resubmit_limit=2")
	var wg sync.WaitGroup
	var accepted atomic.Int32
	for i := 0; i < 30; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			ids, _, e := s.CreateRefunds(testctx, u, fmt.Sprintf("concurrent-%d", i), []refundprovider.Account{account("concurrent@test.invalid")}, 1000)
			if e == nil {
				accepted.Add(1)
				if e = s.Transition(testctx, u, ids[0], "cancelled", "test"); e != nil {
					t.Error(e)
				}
			} else if !errors.Is(e, ErrDuplicate) && !errors.Is(e, ErrAccountRestricted) {
				t.Error(e)
			}
		}(i)
	}
	wg.Wait()
	if accepted.Load() != 2 {
		t.Fatalf("accepted %d, want 2", accepted.Load())
	}
	_, _, e := s.CreateRefunds(testctx, u, ID(), []refundprovider.Account{account("new@test.invalid"), account("concurrent@test.invalid")}, 1000)
	if !errors.Is(e, ErrAccountRestricted) {
		t.Fatal(e)
	}
	var count, frozen int
	s.Pool.QueryRow(testctx, "SELECT count(*) FROM refunds WHERE email='new@test.invalid'").Scan(&count)
	s.Pool.QueryRow(testctx, "SELECT frozen FROM users WHERE id=$1", u).Scan(&frozen)
	if count != 0 || frozen != 0 {
		t.Fatal("rejection changed order or wallet")
	}
}

func TestAccountRestrictionEvidenceAndSettings(t *testing.T) {
	s, admin, u := fixture(t)
	ids, _, _ := s.CreateRefunds(testctx, u, ID(), []refundprovider.Account{account("evidence@test.invalid")}, 1000)
	for _, d := range []RefundDiagnostic{
		{Code: "account_unavailable", HTTPStatus: 403, Evidence: &RefundEvidence{Side: "child", Reason: "unknown_error"}},
		{Code: "account_unavailable", HTTPStatus: 403, Evidence: &RefundEvidence{Side: "mother", Reason: "account_deactivated"}},
		{Code: "verification_deactivated", HTTPStatus: 200, Evidence: &RefundEvidence{Side: "child", Reason: "account_deactivated"}},
		{Code: "login_auth_failed", HTTPStatus: 401, Evidence: &RefundEvidence{Side: "child", Reason: "incorrect_password"}},
	} {
		recordTermination(t, s, ids[0], d)
	}
	var count int
	s.Pool.QueryRow(testctx, "SELECT count(*) FROM refund_account_restrictions").Scan(&count)
	if count != 0 {
		t.Fatal("non-terminal evidence restricted account")
	}
	cfg, _ := s.RefundSettings(testctx)
	if cfg.DeactivatedResubmitLimit == nil || *cfg.DeactivatedResubmitLimit != 0 {
		t.Fatal("default must be zero")
	}
	for _, n := range []int{-1, 11} {
		cfg.DeactivatedResubmitLimit = &n
		if !errors.Is(s.SaveRefundSettings(testctx, admin, cfg), ErrInvalid) {
			t.Fatal("invalid limit accepted")
		}
	}
	n := 3
	cfg.DeactivatedResubmitLimit = &n
	if e := s.SaveRefundSettings(testctx, admin, cfg); e != nil {
		t.Fatal(e)
	}
	cfg.DeactivatedResubmitLimit = nil
	if e := s.SaveRefundSettings(testctx, admin, cfg); e != nil {
		t.Fatal(e)
	}
	cfg, _ = s.RefundSettings(testctx)
	if *cfg.DeactivatedResubmitLimit != 3 {
		t.Fatal("omitted setting reset")
	}
}

func TestAccountRestrictionExistingEvidenceInitialization(t *testing.T) {
	s, _, u := fixture(t)
	original := restrictedAccount(t, s, u, "historical@test.invalid")
	s.Pool.Exec(testctx, "UPDATE refund_settings SET deactivated_resubmit_limit=2")
	ids, _, e := s.CreateRefunds(testctx, u, ID(), []refundprovider.Account{account("historical@test.invalid")}, 1000)
	if e != nil {
		t.Fatal(e)
	}
	if e = s.Transition(testctx, u, ids[0], "cancelled", "test"); e != nil {
		t.Fatal(e)
	}
	// Recreate the pre-upgrade state, with definitive diagnostics and later orders.
	if _, e = s.Pool.Exec(testctx, "DROP TABLE refund_account_restrictions"); e != nil {
		t.Fatal(e)
	}
	raw, e := os.ReadFile("../../../../backend/refund_schema.sql")
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.Pool.Exec(testctx, string(raw)); e != nil {
		t.Fatal(e)
	}
	var used int
	var cause string
	if e = s.Pool.QueryRow(testctx, "SELECT resubmissions,refund_id FROM refund_account_restrictions WHERE email='historical@test.invalid'").Scan(&used, &cause); e != nil {
		t.Fatal(e)
	}
	if used != 1 || cause != original {
		t.Fatalf("used=%d cause=%s", used, cause)
	}
	if _, e = s.Pool.Exec(testctx, string(raw)); e != nil {
		t.Fatal(e)
	}
}
