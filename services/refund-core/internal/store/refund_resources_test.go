package store

import (
	"fmt"
	"llm-refund/internal/refundprovider"
	"sync"
	"testing"
)

func resourceJob(t *testing.T, s *Store, user, email string) *RefundJob {
	t.Helper()
	if _, _, err := s.CreateRefunds(testctx, user, ID(), []refundprovider.Account{account(email)}, 1000); err != nil {
		t.Fatal(err)
	}
	j, err := s.ClaimRefund(testctx)
	if err != nil || j == nil {
		t.Fatal("claim", err)
	}
	return j
}

func resourceSQL(t *testing.T, s *Store, query string, args ...any) {
	t.Helper()
	if _, err := s.Pool.Exec(testctx, query, args...); err != nil {
		t.Fatal(err)
	}
}

func TestResourceResponsibilitySurvivesFeeRelease(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 2)
	resourceSQL(t, s, "UPDATE refund_settings SET recovery=recovery||'{\"enabled\":false}'")
	j := resourceJob(t, s, u, "blocked@test.invalid")
	for _, step := range []string{"inviting", "invited", "login"} {
		checkpoint(t, s, j, step)
	}
	if err := s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "login_failed"}); err != nil {
		t.Fatal(err)
	}
	balance(t, s, u, 10000, 0)
	var state string
	s.Pool.QueryRow(testctx, "SELECT state FROM refund_resource_tasks WHERE refund_id=$1", j.ID).Scan(&state)
	if state != "cleaning" {
		t.Fatal("lost cleanup responsibility", state)
	}
	if _, _, err := s.CreateRefunds(testctx, u, "after-failure", []refundprovider.Account{account("blocked@test.invalid"), account("other@test.invalid")}, 1000); err != nil {
		t.Fatal(err)
	}
	other, err := s.ClaimRefund(testctx)
	if err != nil || other == nil || other.Account.Email != "other@test.invalid" || other.Mother.ID == j.Mother.ID {
		t.Fatal("isolation failed", err)
	}
	if next, err := s.ClaimRefund(testctx); err != nil || next != nil {
		t.Fatal("dirty email crossed mothers", err)
	}
	resourceSQL(t, s, "UPDATE refund_resource_tasks SET state='done' WHERE refund_id=$1", j.ID)
	if next, err := s.ClaimRefund(testctx); err != nil || next == nil || next.Account.Email != "blocked@test.invalid" {
		t.Fatal("cleaned resource not usable", err)
	}
}

func TestCleanedMotherSwitchWaitsObservationAndDoesNotChargeTwice(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 2)
	cfg, err := s.RefundSettings(testctx)
	if err != nil {
		t.Fatal(err)
	}
	cfg.Recovery.Enabled = true
	cfg.Recovery.MaxRetries = 0
	cfg.Recovery.CleanupFailed = true
	cfg.MotherFailover = &MotherFailoverSettings{Enabled: true, MaxSwitches: 2, RetrySeconds: 5}
	if err = s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	j := resourceJob(t, s, u, "switch@test.invalid")
	for _, step := range []string{"inviting", "invited", "login"} {
		checkpoint(t, s, j, step)
	}
	if err = s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "mother_capacity", HTTPStatus: 409}); err != nil {
		t.Fatal(err)
	}
	resourceSQL(t, s, "UPDATE refund_execution SET next_attempt_at=now()-interval '1 second' WHERE refund_id=$1", j.ID)
	clean, err := s.ClaimRefund(testctx)
	if err != nil || clean == nil || clean.RecoveryMode != "cleanup" {
		t.Fatal("missing cleanup", err)
	}
	checkpoint(t, s, clean, "cleanup_started")
	checkpoint(t, s, clean, "cleanup_completed")
	resourceSQL(t, s, "UPDATE refund_execution SET next_attempt_at=now()-interval '1 second' WHERE refund_id=$1", j.ID)
	if next, err := s.ClaimRefund(testctx); err != nil || next != nil {
		t.Fatal("switched before observation", err)
	}
	resourceSQL(t, s, "UPDATE refund_resource_tasks SET state='done' WHERE refund_id=$1", j.ID)
	next, err := s.ClaimRefund(testctx)
	if err != nil || next == nil || next.ID != j.ID || next.Mother.ID == j.Mother.ID {
		t.Fatal("failed safe switch", err)
	}
	balance(t, s, u, 10000, 1000)
	var count int
	s.Pool.QueryRow(testctx, "SELECT count(*) FROM ledger WHERE user_id=$1 AND kind='freeze'", u).Scan(&count)
	if count != 1 {
		t.Fatal("refroze funds", count)
	}
}

func TestResourceCooldownDoesNotDisableMotherAndNewCredentialsRecover(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	cfg, _ := s.RefundSettings(testctx)
	cfg.MotherFailover.Enabled = false
	cfg.Recovery.Enabled = false
	if err := s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	var last *RefundJob
	for i := 0; i < 3; i++ {
		last = resourceJob(t, s, u, fmt.Sprintf("fault%d@test.invalid", i))
		if err := s.FinishRefundAttempt(testctx, last, RefundDiagnostic{Code: "mother_preflight_failed", HTTPStatus: 503}); err != nil {
			t.Fatal(err)
		}
	}
	var enabled, blocked bool
	s.Pool.QueryRow(testctx, "SELECT m.enabled,h.blocked FROM refund_mothers m JOIN refund_resource_health h ON h.scope=m.team_id WHERE m.id=$1", last.Mother.ID).Scan(&enabled, &blocked)
	if !enabled || !blocked {
		t.Fatal("cooldown changed operator enable flag", enabled, blocked)
	}
	if _, _, err := s.CreateRefunds(testctx, u, "waiting-cooldown", []refundprovider.Account{account("waiting@test.invalid")}, 1000); err != nil {
		t.Fatal(err)
	}
	if next, err := s.ClaimRefund(testctx); err != nil || next != nil {
		t.Fatal("cooled mother assigned", err)
	}
	ms, _ := s.RefundMothers(testctx)
	m := ms[0]
	m.AccessToken = "new-offline-token"
	if _, err := s.SaveRefundMother(testctx, a, m); err != nil {
		t.Fatal(err)
	}
	if next, err := s.ClaimRefund(testctx); err != nil || next == nil {
		t.Fatal("new credentials still blocked", err)
	}
}

func TestLoginOutageAllowsOnlyOneProbeAndDoesNotBlockCleanup(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 4)
	cfg, err := s.RefundSettings(testctx)
	if err != nil {
		t.Fatal(err)
	}
	cfg.Recovery.Enabled = false
	cfg.MaxParallel = 4
	cfg.ResourceRecovery.FailureLimit = 1
	cfg.ResourceRecovery.LoginFailureLimit = 2
	cfg.ResourceRecovery.LoginWindowSeconds = 20
	cfg.ResourceRecovery.LoginCooldownSeconds = 45
	if err = s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		j := resourceJob(t, s, u, fmt.Sprintf("login%d@test.invalid", i))
		for _, step := range []string{"inviting", "invited", "login"} {
			checkpoint(t, s, j, step)
		}
		if err := s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "login_failed", HTTPStatus: 500, Evidence: &RefundEvidence{Side: "child", Stage: "mfa_verify", Reason: "login_upstream_unavailable"}}); err != nil {
			t.Fatal(err)
		}
		resourceSQL(t, s, "UPDATE refund_resource_tasks SET state='done'")
	}
	var blocked bool
	s.Pool.QueryRow(testctx, "SELECT blocked FROM refund_resource_health WHERE scope='login'").Scan(&blocked)
	if !blocked {
		t.Fatal("upstream outage did not gate logins")
	}
	var waitSeconds float64
	if err := s.Pool.QueryRow(testctx, "SELECT EXTRACT(EPOCH FROM next_at-now()) FROM refund_resource_health WHERE scope='login'").Scan(&waitSeconds); err != nil || waitSeconds < 40 || waitSeconds > 45 {
		t.Fatal("configured login cooldown not applied", waitSeconds, err)
	}
	var motherFaults int
	s.Pool.QueryRow(testctx, "SELECT count(*) FROM refund_resource_health WHERE scope<>'login'").Scan(&motherFaults)
	if motherFaults != 0 {
		t.Fatal("child outage cooled a mother")
	}
	if _, _, err := s.CreateRefunds(testctx, u, "after-outage", []refundprovider.Account{account("probe1@test.invalid"), account("probe2@test.invalid")}, 1000); err != nil {
		t.Fatal(err)
	}
	if next, err := s.ClaimRefund(testctx); err != nil || next != nil {
		t.Fatal("ignored login delay", err)
	}
	resourceSQL(t, s, "UPDATE refund_resource_health SET next_at=now()-interval '1 second' WHERE scope='login'")
	var wg sync.WaitGroup
	var mu sync.Mutex
	var jobs []*RefundJob
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			j, e := s.ClaimRefund(testctx)
			if e != nil {
				t.Error(e)
			}
			if j != nil {
				mu.Lock()
				jobs = append(jobs, j)
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if len(jobs) != 1 {
		t.Fatal("expected one probe", len(jobs))
	}
	j := jobs[0]
	resourceSQL(t, s, "UPDATE refund_resource_health SET next_at=now()-interval '1 second' WHERE scope='login'")
	if next, err := s.ClaimRefund(testctx); err != nil || next != nil {
		t.Fatal("live probe overlapped", err)
	}
	for _, step := range []string{"inviting", "invited", "login", "merge_v2_ready"} {
		checkpoint(t, s, j, step)
	}
	if next, err := s.ClaimRefund(testctx); err != nil || next == nil {
		t.Fatal("success did not reopen logins", err)
	}
}

func TestPersonalObservationSettingsAndMotherTimeline(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	cfg, _ := s.RefundSettings(testctx)
	if cfg.QueryPersonalPlan == nil || !*cfg.QueryPersonalPlan {
		t.Fatal("personal query must default on")
	}
	off := false
	cfg.QueryPersonalPlan = &off
	if err := s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	cfg.QueryPersonalPlan = nil
	if err := s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	saved, _ := s.RefundSettings(testctx)
	if saved.QueryPersonalPlan == nil || *saved.QueryPersonalPlan {
		t.Fatal("omitted update reset option")
	}
	j := resourceJob(t, s, u, "timeline@test.invalid")
	checkpoint(t, s, j, "mother_check_started")
	checkpoint(t, s, j, "mother_check_completed")
	timeline, err := s.AdminRefundTimeline(testctx, j.ID)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, item := range timeline.Items {
		if item.Stage == "查" && item.Status == "已完成" && item.StartedAt != nil && item.FinishedAt != nil && item.DurationSeconds != nil {
			found = true
		}
	}
	if !found {
		t.Fatal("mother check missing from timeline", timeline.Items)
	}
}

func TestResourceSettingsValidateAndPreserve(t *testing.T) {
	s, a, _ := fixture(t)
	cfg, err := s.RefundSettings(testctx)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ResourceRecovery.LoginFailureLimit != 3 || cfg.ResourceRecovery.LoginWindowSeconds != 60 || cfg.ResourceRecovery.LoginCooldownSeconds != 30 {
		t.Fatal("login defaults", cfg.ResourceRecovery)
	}
	cfg.ResourceRecovery.LoginFailureLimit = 1
	if err = s.SaveRefundSettings(testctx, a, cfg); err == nil {
		t.Fatal("single account must not trip shared outage")
	}
	cfg.ResourceRecovery.LoginFailureLimit = 4
	cfg.ResourceRecovery.LoginWindowSeconds = 120
	cfg.ResourceRecovery.LoginCooldownSeconds = 90
	if err = s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	cfg.ResourceRecovery = nil
	if err = s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	saved, err := s.RefundSettings(testctx)
	if err != nil || saved.ResourceRecovery.LoginFailureLimit != 4 || saved.ResourceRecovery.LoginWindowSeconds != 120 || saved.ResourceRecovery.LoginCooldownSeconds != 90 {
		t.Fatal("omitted config reset", saved, err)
	}
	saved.ResourceRecovery.RetrySeconds = 120
	saved.ResourceRecovery.SlowRetrySeconds = 60
	if err = s.SaveRefundSettings(testctx, a, saved); err == nil {
		t.Fatal("slow retry shorter than normal retry")
	}
}
