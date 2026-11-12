package store

import (
	"errors"
	"llm-refund/internal/refundprovider"
	"sync"
	"testing"
	"time"
)

func childNetwork() RefundDiagnostic {
	return RefundDiagnostic{Code: "login_failed", NetworkCode: 28, Retryable: true, Evidence: &RefundEvidence{Side: "child", Stage: "authorize", Reason: "network_error", Exception: "RuntimeError"}}
}
func backupFixture(t *testing.T, s *Store, a string) (RefundSettings, string) {
	t.Helper()
	cfg, e := s.RefundSettings(testctx)
	if e != nil {
		t.Fatal(e)
	}
	id, e := s.SaveRefundProxy(testctx, a, RefundProxy{Name: "Second provider", URL: "http://backup.invalid:8080", Target: "child", Enabled: true})
	if e != nil {
		t.Fatal(e)
	}
	cfg.ChildProxyBackups = []string{id}
	cfg.ChildProxyCooldownSeconds = 300
	if e = s.SaveRefundSettings(testctx, a, cfg); e != nil {
		t.Fatal(e)
	}
	return cfg, id
}
func due(t *testing.T, s *Store, id string) {
	t.Helper()
	if _, e := s.Pool.Exec(testctx, "UPDATE refund_execution SET next_attempt_at=now()-interval '1 second' WHERE refund_id=$1", id); e != nil {
		t.Fatal(e)
	}
}

func TestChildProxyFailureSwitchesRetryAndNewOrdersAvoidCooling(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 2)
	cfg, backup := backupFixture(t, s, a)
	if _, _, e := s.CreateRefunds(testctx, u, "child-failover", []refundprovider.Account{account("one@fixture.invalid"), account("two@fixture.invalid")}, 1000); e != nil {
		t.Fatal(e)
	}
	j, e := s.ClaimRefund(testctx)
	if e != nil || j == nil {
		t.Fatal(e)
	}
	if j.DiagnosticContext.ChildProxyID != cfg.ChildProxyID {
		t.Fatal("did not start primary")
	}
	for _, step := range []string{"inviting", "invited", "login"} {
		checkpoint(t, s, j, step)
	}
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if e := s.FinishRefundAttempt(testctx, j, childNetwork()); e != nil {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	due(t, s, j.ID)
	retry, e := s.ClaimRefund(testctx)
	if e != nil || retry == nil {
		t.Fatal(e)
	}
	if retry.ID != j.ID || retry.DiagnosticContext.ChildProxyID != backup || retry.MotherProxy != j.MotherProxy || retry.Step != "login" {
		t.Fatal("retry identity/step/exit changed incorrectly")
	}
	var switches int
	s.Pool.QueryRow(testctx, "SELECT child_proxy_switches FROM refund_execution WHERE refund_id=$1", j.ID).Scan(&switches)
	if switches != 1 {
		t.Fatal("duplicate failover", switches)
	}
	next, e := s.ClaimRefund(testctx)
	if e != nil || next == nil || next.DiagnosticContext.ChildProxyID != backup {
		t.Fatal("new order reused failed primary", e)
	}
	balance(t, s, u, 10000, 2000)
	// A restart retains the backup binding, with no switch back mid-order.
	s.Pool.Exec(testctx, "UPDATE refund_execution SET lease_token='',lease_until=NULL WHERE refund_id=$1", retry.ID)
	restarted, e := s.ClaimRefund(testctx)
	if e != nil || restarted == nil || restarted.ChildProxy != retry.ChildProxy {
		t.Fatal("restart lost binding", e)
	}
	for _, step := range []string{"merge_v2_ready", "merge_v2_submitting", "merge_v2_accepted", "removing", "completed"} {
		checkpoint(t, s, restarted, step)
	}
	balance(t, s, u, 9000, 1000)
	// A manual check cannot bypass automatic recovery or its cooldown.
	if e = s.SaveResourceCheck(testctx, a, "proxy", cfg.ChildProxyID, time.Now().Add(time.Second), ResourceCheck{Valid: true, CheckedAt: time.Now()}); e != nil {
		t.Fatal(e)
	}
	s.Pool.Exec(testctx, "UPDATE refund_proxies SET failure_until=now()-interval '1 second' WHERE id=$1", cfg.ChildProxyID)
	for i := 0; i < 3; i++ {
		recoverChildSample(t, s, true)
	}
	if _, _, e = s.CreateRefunds(testctx, u, "child-third", []refundprovider.Account{account("three@fixture.invalid")}, 1000); e != nil {
		t.Fatal(e)
	}
	third, e := s.ClaimRefund(testctx)
	if e != nil || third == nil || third.DiagnosticContext.ChildProxyID != cfg.ChildProxyID {
		t.Fatal("primary not restored", e)
	}
}

func TestChildProxySwitchAfterUncertainSubmissionOnlyVerifies(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	_, backup := backupFixture(t, s, a)
	s.CreateRefunds(testctx, u, "uncertain-proxy", []refundprovider.Account{account("uncertain@fixture.invalid")}, 1000)
	j, e := s.ClaimRefund(testctx)
	if e != nil || j == nil {
		t.Fatal(e)
	}
	for _, step := range []string{"inviting", "invited", "login", "merge_v2_ready", "merge_v2_submitting"} {
		checkpoint(t, s, j, step)
	}
	d := childNetwork()
	d.Code = "export_uncertain"
	d.Evidence.Stage = "export"
	if e = s.FinishRefundAttempt(testctx, j, d); e != nil {
		t.Fatal(e)
	}
	due(t, s, j.ID)
	retry, e := s.ClaimRefund(testctx)
	if e != nil || retry == nil {
		t.Fatal(e)
	}
	if retry.Step != "submitting" || retry.DiagnosticContext.ChildProxyID != backup || retry.RecoveryMode != "fallback" {
		t.Fatal("submission fence lost", retry.Step, retry.RecoveryMode)
	}
	if e = s.RefundCheckpoint(testctx, retry, Checkpoint{Method: "merge_v2_submitting", TeamID: retry.Mother.TeamID}); !errors.Is(e, ErrState) {
		t.Fatal("replayed submission", e)
	}
	balance(t, s, u, 10000, 1000)
}

func TestChildFailureClassifierExcludesAccountAndMotherFailures(t *testing.T) {
	for _, d := range []RefundDiagnostic{
		{Code: "login_auth_failed", HTTPStatus: 401, Evidence: &RefundEvidence{Side: "child", Reason: "login_auth_failed"}},
		{Code: "login_failed", HTTPStatus: 429, Evidence: &RefundEvidence{Side: "child", Reason: "chatgpt_rate_limited"}},
		{Code: "mother_network", NetworkCode: 28, Evidence: &RefundEvidence{Side: "mother", Reason: "network_error"}},
		{Code: "extra_verification", Evidence: &RefundEvidence{Side: "child", Reason: "extra_verification"}},
	} {
		if childProxyNetworkFailure(d) {
			t.Fatal("not a child network fault", d.Code)
		}
	}
	if !childProxyNetworkFailure(childNetwork()) {
		t.Fatal("missed curl timeout")
	}
	if !childProxyNetworkFailure(RefundDiagnostic{Code: "login_failed", Evidence: &RefundEvidence{Side: "child", ProxyHTTPStatus: 504}}) {
		t.Fatal("missed proxy 504")
	}
}

func recoverChildSample(t *testing.T, s *Store, passed bool) *ChildProxyRecovery {
	t.Helper()
	s.Pool.Exec(testctx, "UPDATE refund_proxies SET recovery_checked_at=NULL")
	j, err := s.ClaimChildProxyRecovery(testctx)
	if err != nil || j == nil {
		t.Fatal("probe", err)
	}
	if err = s.FinishChildProxyRecovery(testctx, j, passed); err != nil {
		t.Fatal(err)
	}
	return j
}

func TestAllChildProxiesCoolingWaitForThreeIndependentChecks(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 2)
	cfg, backup := backupFixture(t, s, a)
	s.CreateRefunds(testctx, u, "cooldown", []refundprovider.Account{account("cool1@fixture.invalid"), account("cool2@fixture.invalid")}, 1000)
	s.Pool.Exec(testctx, "UPDATE refund_proxies SET failure_at=now(),failure_until=now()+interval '5 minutes' WHERE id=ANY($1)", []string{cfg.ChildProxyID, backup})
	if j, err := s.ClaimChildProxyRecovery(testctx); err != nil || j != nil {
		t.Fatal("probed before cooldown", err)
	}
	s.Pool.Exec(testctx, "UPDATE refund_proxies SET failure_until=now()-interval '1 second' WHERE id=$1", cfg.ChildProxyID)
	for i := 0; i < 3; i++ {
		if j, err := s.ClaimRefund(testctx); err != nil || j != nil {
			t.Fatal("order used unverified exit", err)
		}
		sample := recoverChildSample(t, s, true)
		if err := s.FinishChildProxyRecovery(testctx, sample, true); err != nil {
			t.Fatal(err)
		}
	}
	for i := 0; i < 2; i++ {
		if j, err := s.ClaimRefund(testctx); err != nil || j == nil || j.DiagnosticContext.ChildProxyID != cfg.ChildProxyID {
			t.Fatal("recovered primary unavailable", err)
		}
	}
	balance(t, s, u, 10000, 2000)
}

func TestChildRecoveryFailureBlankAndStaleResults(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 2)
	cfg, _ := backupFixture(t, s, a)
	s.Pool.Exec(testctx, "UPDATE refund_proxies SET failure_at=now(),failure_until=now()-interval '1 second' WHERE id=$1", cfg.ChildProxyID)
	recoverChildSample(t, s, true)
	recoverChildSample(t, s, false)
	var n int
	var cooling bool
	s.Pool.QueryRow(testctx, "SELECT recovery_successes,failure_until>now()+interval '290 seconds' FROM refund_proxies WHERE id=$1", cfg.ChildProxyID).Scan(&n, &cooling)
	if n != 0 || !cooling {
		t.Fatal("failure did not reset and cool")
	}
	s.Pool.Exec(testctx, "UPDATE refund_proxies SET failure_until=now()-interval '1 second',recovery_checked_at=NULL WHERE id=$1", cfg.ChildProxyID)
	job, err := s.ClaimChildProxyRecovery(testctx)
	if err != nil || job == nil {
		t.Fatal(err)
	}
	s.Pool.Exec(testctx, "UPDATE refund_proxies SET failure_at=now(),failure_until=now()+interval '5 minutes' WHERE id=$1", cfg.ChildProxyID)
	if err = s.FinishChildProxyRecovery(testctx, job, true); err != nil {
		t.Fatal(err)
	}
	s.Pool.QueryRow(testctx, "SELECT recovery_successes FROM refund_proxies WHERE id=$1", cfg.ChildProxyID).Scan(&n)
	if n != 0 {
		t.Fatal("old success erased newer failure")
	}
	cfg.ChildProxyRecoverySuccessCount = nil
	if err = s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	s.Pool.Exec(testctx, "UPDATE refund_proxies SET failure_until=now()-interval '1 second' WHERE id=$1", cfg.ChildProxyID)
	if j, err := s.ClaimChildProxyRecovery(testctx); err != nil || j != nil {
		t.Fatal("blank still tested", err)
	}
	var failed bool
	s.Pool.QueryRow(testctx, "SELECT failure_at IS NOT NULL FROM refund_proxies WHERE id=$1", cfg.ChildProxyID).Scan(&failed)
	if failed {
		t.Fatal("blank did not restore")
	}
	balance(t, s, u, 10000, 0)
}

func TestChildRecoveryClaimConcurrentAndConfigFenced(t *testing.T) {
	s, a, _ := fixture(t)
	configure(t, s, a, 1)
	cfg, _ := backupFixture(t, s, a)
	if cfg.ChildProxyRecoverySuccessCount == nil || *cfg.ChildProxyRecoverySuccessCount != 3 {
		t.Fatal("default must be three")
	}
	s.Pool.Exec(testctx, "UPDATE refund_proxies SET failure_at=now(),failure_until=now()-interval '1 second' WHERE id=$1", cfg.ChildProxyID)
	jobs := make(chan *ChildProxyRecovery, 20)
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			j, err := s.ClaimChildProxyRecovery(testctx)
			if err != nil {
				t.Error(err)
			}
			if j != nil {
				jobs <- j
			}
		}()
	}
	wg.Wait()
	close(jobs)
	if len(jobs) != 1 {
		t.Fatal("concurrent probes", len(jobs))
	}
	job := <-jobs
	required := 5
	cfg.ChildProxyRecoverySuccessCount = &required
	if err := s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	if err := s.FinishChildProxyRecovery(testctx, job, true); err != nil {
		t.Fatal(err)
	}
	var n int
	s.Pool.QueryRow(testctx, "SELECT recovery_successes FROM refund_proxies WHERE id=$1", cfg.ChildProxyID).Scan(&n)
	if n != 0 {
		t.Fatal("changed configuration accepted stale probe")
	}
	for _, required = range []int{0, 21} {
		cfg.ChildProxyRecoverySuccessCount = &required
		if err := s.SaveRefundSettings(testctx, a, cfg); err == nil {
			t.Fatal("invalid threshold")
		}
	}
}

func TestRefundProxyTargetEnforcedAndInvalidBackupRejected(t *testing.T) {
	s, a, _ := fixture(t)
	configure(t, s, a, 1)
	cfg, backup := backupFixture(t, s, a)
	mothers, _ := s.RefundMothers(testctx)
	mother := mothers[0]
	mother.ProxyID = backup
	if _, e := s.SaveRefundMother(testctx, a, mother); e == nil {
		t.Fatal("child-only selected for mother")
	}
	cfg.ChildProxyBackups = []string{cfg.ChildProxyID}
	if e := s.SaveRefundSettings(testctx, a, cfg); e == nil {
		t.Fatal("duplicate main/backup")
	}
	cfg.ChildProxyBackups = []string{"missing"}
	if e := s.SaveRefundSettings(testctx, a, cfg); e == nil {
		t.Fatal("invalid backup")
	}
	if _, e := s.SaveRefundProxy(testctx, a, RefundProxy{ID: backup, Name: "Mother only", Target: "mother", Enabled: true}); e != nil {
		t.Fatal(e)
	}
	cfg, _ = s.RefundSettings(testctx)
	if len(cfg.ChildProxyBackups) != 0 {
		t.Fatal("ineligible backup retained")
	}
	if _, e := s.SaveRefundProxy(testctx, a, RefundProxy{Name: "Bad", Target: "unknown", URL: "http://bad.invalid:80"}); e == nil {
		t.Fatal("invalid target")
	}
}

func TestCoolingChildDoesNotBlockMotherOnlyCompletion(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 2)
	cfg, backup := backupFixture(t, s, a)
	s.CreateRefunds(testctx, u, "cleanup-behind-cooling", []refundprovider.Account{account("waiting@fixture.invalid"), account("finishing@fixture.invalid")}, 1000)
	first, err := s.ClaimRefund(testctx)
	if err != nil || first == nil {
		t.Fatal(err)
	}
	second, err := s.ClaimRefund(testctx)
	if err != nil || second == nil {
		t.Fatal(err)
	}
	for _, step := range []string{"inviting", "invited", "login", "merge_v2_ready", "merge_v2_submitting", "merge_v2_accepted"} {
		checkpoint(t, s, second, step)
	}
	for _, step := range []string{"inviting", "invited", "login"} {
		checkpoint(t, s, first, step)
	}
	if err = s.FinishRefundAttempt(testctx, first, childNetwork()); err != nil {
		t.Fatal(err)
	}
	due(t, s, first.ID)
	s.Pool.Exec(testctx, "UPDATE refund_proxies SET failure_at=now(),failure_until=now()+interval '5 minutes' WHERE id=ANY($1)", []string{cfg.ChildProxyID, backup})
	s.Pool.Exec(testctx, "UPDATE refund_execution SET lease_token='',lease_until=NULL,updated_at=now()+interval '1 second' WHERE refund_id=$1", second.ID)
	finishing, err := s.ClaimRefund(testctx)
	if err != nil || finishing == nil || finishing.ID != second.ID || finishing.Step != "accepted" {
		t.Fatal("cooling blocked mother-only completion", err)
	}
	checkpoint(t, s, finishing, "removing")
	checkpoint(t, s, finishing, "completed")
	balance(t, s, u, 9000, 1000)
}

func TestChildRecoveryIntervalStartsAfterFinishedCheck(t *testing.T) {
	s, a, _ := fixture(t)
	configure(t, s, a, 1)
	cfg, _ := backupFixture(t, s, a)
	cfg.ChildProxyRecoveryIntervalSeconds = 12
	if err := s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	s.Pool.Exec(testctx, "UPDATE refund_proxies SET failure_at=now(),failure_until=now()-interval '1 second' WHERE id=$1", cfg.ChildProxyID)
	recoverChildSample(t, s, true)
	if j, err := s.ClaimChildProxyRecovery(testctx); err != nil || j != nil {
		t.Fatal("no interval after result", err)
	}
	s.Pool.Exec(testctx, "UPDATE refund_proxies SET recovery_checked_at=now()-interval '11 seconds' WHERE id=$1", cfg.ChildProxyID)
	if j, err := s.ClaimChildProxyRecovery(testctx); err != nil || j != nil {
		t.Fatal("configured interval ignored", err)
	}
	s.Pool.Exec(testctx, "UPDATE refund_proxies SET recovery_checked_at=now()-interval '13 seconds' WHERE id=$1", cfg.ChildProxyID)
	if j, err := s.ClaimChildProxyRecovery(testctx); err != nil || j == nil {
		t.Fatal("probe not due", err)
	}
}
