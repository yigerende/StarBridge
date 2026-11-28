package store

import (
	"errors"
	"llm-refund/internal/refundprovider"
	"sync"
	"sync/atomic"
	"testing"
)

func TestFinalPlanConcurrentFreeCallbacksSettleOnce(t *testing.T) {
	s, _, u, j := finalPlanFixture(t)
	resourceSQL(t, s, "UPDATE refund_execution SET fallback_cycles=2 WHERE refund_id=$1", j.ID)
	if err := s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "export_uncertain", HTTPStatus: 500}); err != nil {
		t.Fatal(err)
	}
	j = duePlan(t, s, j.ID)
	checkFinalPlan(t, s, j)
	for _, method := range []string{"merge_v2_submitting", "merge_v2_rejected", "inviting", "login"} {
		if err := s.RefundCheckpoint(testctx, j, Checkpoint{Method: method, TeamID: j.Mother.TeamID, UserID: "offline-child"}); !errors.Is(err, ErrState) {
			t.Fatal("final check replay allowed", method, err)
		}
	}
	var wg sync.WaitGroup
	var success atomic.Int32
	for n := 0; n < 24; n++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			err := s.RefundCheckpoint(testctx, j, Checkpoint{Method: "fallback_free", TeamID: j.Mother.TeamID, UserID: "offline-child"})
			if err == nil {
				success.Add(1)
			} else if !errors.Is(err, ErrState) {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	if success.Load() != 1 {
		t.Fatal("accepted duplicate callbacks", success.Load())
	}
	balance(t, s, u, 9000, 0)
	var count int
	s.Pool.QueryRow(testctx, "SELECT count(*) FROM ledger WHERE refund_order_id=$1 AND kind='charge'", j.ID).Scan(&count)
	if count != 1 {
		t.Fatal("duplicate charge", count)
	}
}

func finalPlanFixture(t *testing.T) (*Store, string, string, *RefundJob) {
	t.Helper()
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	cfg, err := s.RefundSettings(testctx)
	if err != nil {
		t.Fatal(err)
	}
	cfg.Recovery.Enabled = true
	cfg.Recovery.PlanFallback = &PlanFallbackSettings{Enabled: true, MaxCycles: 2, MaxAttempts: 2, RetrySeconds: 5}
	if err = s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	j := resourceJob(t, s, u, "final@fixture.invalid")
	submitFinalPlan(t, s, j)
	return s, a, u, j
}

func submitFinalPlan(t *testing.T, s *Store, j *RefundJob) {
	t.Helper()
	for _, step := range []string{"inviting", "invited", "login", "merge_v2_ready", "merge_v2_submitting"} {
		checkpoint(t, s, j, step)
	}
}

func duePlan(t *testing.T, s *Store, id string) *RefundJob {
	t.Helper()
	resourceSQL(t, s, "UPDATE refund_execution SET next_attempt_at=now()-interval '1 second' WHERE refund_id=$1", id)
	j, err := s.ClaimRefund(testctx)
	if err != nil || j == nil || j.ID != id {
		t.Fatalf("missing original recovery: %v %+v", err, j)
	}
	return j
}

func checkFinalPlan(t *testing.T, s *Store, j *RefundJob) {
	t.Helper()
	for _, step := range []string{"fallback_removing", "fallback_removed", "fallback_login", "fallback_plan_started"} {
		checkpoint(t, s, j, step)
	}
}

func TestFinalPlanEverySubmissionCheckedWithoutExtraReinvite(t *testing.T) {
	for _, outcome := range []string{"paid", "free"} {
		t.Run(outcome, func(t *testing.T) {
			s, _, u, j := finalPlanFixture(t)
			for round := 0; round < 3; round++ {
				if err := s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "export_uncertain", HTTPStatus: 500}); err != nil {
					t.Fatal(err)
				}
				j = duePlan(t, s, j.ID)
				if j.RecoveryMode != "fallback" {
					t.Fatal("last submission skipped plan check", j.RecoveryMode)
				}
				checkFinalPlan(t, s, j)
				result := "paid"
				if round == 2 {
					result = outcome
				}
				checkpoint(t, s, j, "fallback_"+result)
				if err := s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "fallback_" + result, Retryable: true}); err != nil {
					t.Fatal(err)
				}
				if round < 2 {
					j = duePlan(t, s, j.ID)
					submitFinalPlan(t, s, j)
				}
			}
			r, err := s.Refund(testctx, u, j.ID)
			if err != nil {
				t.Fatal(err)
			}
			if outcome == "free" {
				if r.Status != "succeeded" || r.FeeStatus != "settled" {
					t.Fatal(r)
				}
				balance(t, s, u, 9000, 0)
			} else {
				if r.Status != "failed" || r.FeeStatus != "released" || r.Message != finalPaidPlanMessage {
					t.Fatal(r)
				}
				balance(t, s, u, 10000, 0)
				var state string
				var clean bool
				s.Pool.QueryRow(testctx, "SELECT t.state,e.mother_released_at IS NOT NULL FROM refund_resource_tasks t JOIN refund_execution e ON e.refund_id=t.refund_id WHERE t.refund_id=$1", j.ID).Scan(&state, &clean)
				if state != "watching" || !clean {
					t.Fatal("cleanup abandoned", state, clean)
				}
			}
			var wg sync.WaitGroup
			for n := 0; n < 24; n++ {
				wg.Add(1)
				go func() {
					defer wg.Done()
					if e := s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "worker_interrupted"}); e != nil {
						t.Error(e)
					}
				}()
			}
			wg.Wait()
			var charges, unfreezes int
			s.Pool.QueryRow(testctx, "SELECT count(*) FILTER(WHERE kind='charge'),count(*) FILTER(WHERE kind='unfreeze') FROM ledger WHERE refund_order_id=$1", j.ID).Scan(&charges, &unfreezes)
			if charges+unfreezes != 1 {
				t.Fatal("duplicate settlement", charges, unfreezes)
			}
			if next, err := s.ClaimRefund(testctx); err != nil || next != nil {
				t.Fatal("unexpected extra attempt", err)
			}
		})
	}
}

func TestFinalPlanUnknownRetainsTargetButReleasesCleanMother(t *testing.T) {
	s, a, u, j := finalPlanFixture(t)
	resourceSQL(t, s, "UPDATE refund_execution SET fallback_cycles=2 WHERE refund_id=$1", j.ID)
	if err := s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "export_uncertain", HTTPStatus: 500}); err != nil {
		t.Fatal(err)
	}
	j = duePlan(t, s, j.ID)
	checkFinalPlan(t, s, j)
	if err := s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "fallback_unavailable", NetworkCode: 28}); err != nil {
		t.Fatal(err)
	}
	j = duePlan(t, s, j.ID)
	if j.FallbackPhase != "checking" {
		t.Fatal("lost removal progress")
	}
	if err := s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "fallback_unavailable", NetworkCode: 28}); err != nil {
		t.Fatal(err)
	}
	r, _ := s.Refund(testctx, u, j.ID)
	if r.Status != "attention" || r.FeeStatus != "frozen" || r.Message != finalPlanUnknownMessage {
		t.Fatal(r)
	}
	balance(t, s, u, 10000, 1000)
	if next, err := s.ClaimRefund(testctx); err != nil || next != nil {
		t.Fatal("unbounded automatic check", err)
	}
	// A different customer cannot run the same target while its result is unknown.
	_, _, err := s.CreateRefunds(testctx, a, ID(), []refundprovider.Account{account(j.Account.Email)}, 1000)
	if err == nil {
		t.Fatal("unresolved target accepted across customers")
	}
	_, _, err = s.CreateRefunds(testctx, a, ID(), []refundprovider.Account{account("unrelated@fixture.invalid")}, 1000)
	if err != nil {
		t.Fatal(err)
	}
	other, err := s.ClaimRefund(testctx)
	if err != nil || other == nil || other.Account.Email != "unrelated@fixture.invalid" {
		t.Fatal("clean mother not usable", err)
	}
	submitFinalPlan(t, s, other)
	for _, v := range []string{"merge_v2_accepted", "removing", "completed"} {
		checkpoint(t, s, other, v)
	}
	if err = s.ResumeRefund(testctx, a, j.ID, "confirm_rejected", "offline verification fixture"); !errors.Is(err, ErrInvalid) {
		t.Fatal("final check allowed new POST", err)
	}
	if err = s.ResumeRefund(testctx, a, j.ID, "verify_only", "offline verification fixture"); err != nil {
		t.Fatal(err)
	}
	j = duePlan(t, s, j.ID)
	if j.RecoveryMode != "fallback" || j.FallbackPhase != "checking" {
		t.Fatal("resume replayed submission")
	}
	checkpoint(t, s, j, "fallback_login")
	checkpoint(t, s, j, "fallback_plan_started")
	checkpoint(t, s, j, "fallback_paid")
	r, _ = s.Refund(testctx, u, j.ID)
	if r.Status != "failed" {
		t.Fatal(r)
	}
	balance(t, s, u, 10000, 0)
	if _, _, err = s.CreateRefunds(testctx, a, ID(), []refundprovider.Account{account(j.Account.Email)}, 1000); err != nil {
		t.Fatal(err)
	}
	if next, err := s.ClaimRefund(testctx); err != nil || next != nil {
		t.Fatal("next target ran before cleanup observation", err)
	}
	resourceSQL(t, s, "UPDATE refund_resource_tasks SET state='done' WHERE refund_id=$1", j.ID)
	if next, err := s.ClaimRefund(testctx); err != nil || next == nil || next.Account.Email != j.Account.Email {
		t.Fatal("next target remained stuck", err)
	}
}

func TestFinalPlanRestartAndRemovalFailureDoNotInventCleanup(t *testing.T) {
	s, _, u, j := finalPlanFixture(t)
	resourceSQL(t, s, "UPDATE refund_execution SET fallback_cycles=2 WHERE refund_id=$1", j.ID)
	if err := s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "export_uncertain", HTTPStatus: 500}); err != nil {
		t.Fatal(err)
	}
	j = duePlan(t, s, j.ID)
	checkpoint(t, s, j, "fallback_removing")
	resourceSQL(t, s, "UPDATE refund_execution SET lease_until=now()-interval '1 second' WHERE refund_id=$1", j.ID)
	if next, err := s.ClaimRefund(testctx); err != nil || next != nil {
		t.Fatal("restart should schedule recovery", err)
	}
	j = duePlan(t, s, j.ID)
	if j.FallbackPhase != "removing" {
		t.Fatal("lost removal phase")
	}
	if err := s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "fallback_unavailable", NetworkCode: 28}); err != nil {
		t.Fatal(err)
	}
	r, _ := s.Refund(testctx, u, j.ID)
	if r.Status != "attention" {
		t.Fatal(r)
	}
	var clean bool
	s.Pool.QueryRow(testctx, "SELECT mother_released_at IS NOT NULL FROM refund_execution WHERE refund_id=$1", j.ID).Scan(&clean)
	if clean {
		t.Fatal("failed removal released mother")
	}
	balance(t, s, u, 10000, 1000)
}
