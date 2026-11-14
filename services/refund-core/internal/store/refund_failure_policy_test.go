package store

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"llm-refund/internal/refundprovider"
	"strings"
	"sync"
	"testing"
)

func teamUnavailable() RefundDiagnostic {
	return RefundDiagnostic{Code: "login_failed", HTTPStatus: 200, Retryable: true, Evidence: &RefundEvidence{Side: "child", Stage: "session", Reason: "team_session_not_ready"}}
}
func reachLogin(t *testing.T, s *Store, j *RefundJob) {
	t.Helper()
	for _, step := range []string{"inviting", "invited", "login"} {
		checkpoint(t, s, j, step)
	}
}
func finishSafeCleanup(t *testing.T, s *Store, id string) {
	t.Helper()
	due(t, s, id)
	j, e := s.ClaimRefund(testctx)
	if e != nil || j == nil || j.ID != id || j.RecoveryMode != "cleanup" {
		t.Fatal("cleanup missing", e)
	}
	checkpoint(t, s, j, "cleanup_started")
	checkpoint(t, s, j, "cleanup_completed")
}

func TestFailurePolicySwitchesOriginalOrderOnlyAfterCleanup(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 2)
	cfg, _ := s.RefundSettings(testctx)
	cfg.Recovery.MaxRetries = 5
	cfg.ResourceRecovery.TeamSessionFailureLimit = 2
	if e := s.SaveRefundSettings(testctx, a, cfg); e != nil {
		t.Fatal(e)
	}
	j := resourceJob(t, s, u, "switch-team@fixture.invalid")
	reachLogin(t, s, j)
	original := j
	for i := 0; i < 2; i++ {
		if e := s.FinishRefundAttempt(testctx, j, teamUnavailable()); e != nil {
			t.Fatal(e)
		}
		if e := s.FinishRefundAttempt(testctx, j, teamUnavailable()); e != nil {
			t.Fatal(e)
		} // duplicate completion
		if i == 0 {
			due(t, s, j.ID)
			j, _ = s.ClaimRefund(testctx)
			if j == nil || j.Mother.ID != original.Mother.ID || j.RecoveryMode != "retry" {
				t.Fatal("first failure changed mother")
			}
		}
	}
	balance(t, s, u, 10000, 1000)
	finishSafeCleanup(t, s, j.ID)
	due(t, s, j.ID)
	if next, e := s.ClaimRefund(testctx); e != nil || next != nil {
		t.Fatal("switched before delayed-member observation", e)
	}
	resourceSQL(t, s, "UPDATE refund_resource_tasks SET state='done' WHERE refund_id=$1", j.ID)
	next, e := s.ClaimRefund(testctx)
	if e != nil || next == nil || next.ID != original.ID || next.Mother.ID == original.Mother.ID {
		t.Fatal("original order did not switch", e)
	}
	if next.DiagnosticContext.Retry != 0 {
		t.Fatal("new mother inherited exhausted login budget")
	}
	for _, step := range []string{"inviting", "invited", "login", "merge_v2_ready", "merge_v2_submitting", "merge_v2_accepted", "removing", "completed"} {
		checkpoint(t, s, next, step)
	}
	balance(t, s, u, 9000, 0)
	var freezes, charges int
	s.Pool.QueryRow(testctx, "SELECT count(*) FILTER(WHERE kind='freeze'),count(*) FILTER(WHERE kind='charge') FROM ledger WHERE refund_order_id=$1", j.ID).Scan(&freezes, &charges)
	if freezes != 1 || charges != 1 {
		t.Fatal("duplicate settlement", freezes, charges)
	}
}

func TestFailurePolicyStopsWhenNoOtherMotherRemains(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	resourceSQL(t, s, `UPDATE refund_settings SET resource_recovery=resource_recovery||'{"team_session_failure_limit":1}'`)
	j := resourceJob(t, s, u, "no-alternative@fixture.invalid")
	reachLogin(t, s, j)
	if e := s.FinishRefundAttempt(testctx, j, teamUnavailable()); e != nil {
		t.Fatal(e)
	}
	finishSafeCleanup(t, s, j.ID)
	balance(t, s, u, 10000, 0)
	var status, mode string
	var count int
	s.Pool.QueryRow(testctx, "SELECT r.status,e.recovery_mode,b.failure_count FROM refunds r JOIN refund_execution e ON e.refund_id=r.id JOIN refund_account_restrictions b ON b.email=r.email WHERE r.id=$1", j.ID).Scan(&status, &mode, &count)
	if status != "failed" || mode != "" || count != 1 {
		t.Fatal("exhausted mothers queued forever", status, mode, count)
	}
}

func TestFailurePolicyDoesNotCrossSubmissionFence(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 2)
	resourceSQL(t, s, `UPDATE refund_settings SET resource_recovery=resource_recovery||'{"team_session_failure_limit":1}'`)
	j := resourceJob(t, s, u, "submitted@fixture.invalid")
	reachLogin(t, s, j)
	for _, step := range []string{"merge_v2_ready", "merge_v2_submitting"} {
		checkpoint(t, s, j, step)
	}
	if e := s.FinishRefundAttempt(testctx, j, teamUnavailable()); e != nil {
		t.Fatal(e)
	}
	var switches, count int
	var step string
	s.Pool.QueryRow(testctx, "SELECT step,mother_switch_count FROM refund_execution WHERE refund_id=$1", j.ID).Scan(&step, &switches)
	s.Pool.QueryRow(testctx, "SELECT count(*) FROM refund_mother_failure_accounts").Scan(&count)
	if step != "submitting" || switches != 0 || count != 0 {
		t.Fatal("unknown submission was switched", step, switches, count)
	}
	balance(t, s, u, 10000, 1000)
}

func TestMotherFailurePolicyDistinctAccountsAndManualReset(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	resourceSQL(t, s, `UPDATE refund_settings SET resource_recovery=resource_recovery||'{"team_session_failure_limit":1}',mother_failover=mother_failover||'{"enabled":false}'`)
	var mother string
	for i, email := range []string{"one@fixture.invalid", "one@fixture.invalid", "two@fixture.invalid"} {
		j := resourceJob(t, s, u, email)
		mother = j.Mother.ID
		reachLogin(t, s, j)
		if e := s.FinishRefundAttempt(testctx, j, teamUnavailable()); e != nil {
			t.Fatal(e)
		}
		finishSafeCleanup(t, s, j.ID)
		resourceSQL(t, s, "UPDATE refund_resource_tasks SET state='done'")
		ms, e := s.RefundMothers(testctx)
		if e != nil {
			t.Fatal(e)
		}
		if ms[0].Enabled != (i < 2) {
			t.Fatal("must count different accounts", i, ms[0].Enabled)
		}
		if i == 2 && (ms[0].AutoDisabledReason == "" || ms[0].AutoDisabledAt == nil) {
			t.Fatal("disabled reason missing")
		}
	}
	ms, _ := s.RefundMothers(testctx)
	ms[0].Name = "Renamed"
	if _, e := s.SaveRefundMother(testctx, a, ms[0]); e != nil {
		t.Fatal(e)
	}
	ms, _ = s.RefundMothers(testctx)
	if ms[0].AutoDisabledReason == "" {
		t.Fatal("rename cleared cause")
	}
	ms[0].Enabled = true
	if _, e := s.SaveRefundMother(testctx, a, ms[0]); e != nil {
		t.Fatal(e)
	}
	ms, _ = s.RefundMothers(testctx)
	if ms[0].AutoDisabledReason != "" {
		t.Fatal("manual enable did not clear cause")
	}
	j := resourceJob(t, s, u, "three@fixture.invalid")
	reachLogin(t, s, j)
	if j.Mother.ID != mother {
		t.Fatal("unexpected mother")
	}
	if e := s.FinishRefundAttempt(testctx, j, teamUnavailable()); e != nil {
		t.Fatal(e)
	}
	ms, _ = s.RefundMothers(testctx)
	if !ms[0].Enabled {
		t.Fatal("manual reset kept old failures")
	}
}

func TestMotherFailureSuccessAndCredentialVersionReset(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	resourceSQL(t, s, `UPDATE refund_settings SET resource_recovery=resource_recovery||'{"team_session_failure_limit":1}',mother_failover=mother_failover||'{"enabled":false}'`)
	j := resourceJob(t, s, u, "failed@fixture.invalid")
	reachLogin(t, s, j)
	if e := s.FinishRefundAttempt(testctx, j, teamUnavailable()); e != nil {
		t.Fatal(e)
	}
	finishSafeCleanup(t, s, j.ID)
	resourceSQL(t, s, "UPDATE refund_resource_tasks SET state='done'")
	good := resourceJob(t, s, u, "good@fixture.invalid")
	reachLogin(t, s, good)
	for _, step := range []string{"merge_v2_ready", "merge_v2_submitting", "merge_v2_accepted", "removing", "completed"} {
		checkpoint(t, s, good, step)
	}
	resourceSQL(t, s, "UPDATE refund_resource_tasks SET state='done'")
	next := resourceJob(t, s, u, "another@fixture.invalid")
	reachLogin(t, s, next)
	if e := s.FinishRefundAttempt(testctx, next, teamUnavailable()); e != nil {
		t.Fatal(e)
	}
	ms, _ := s.RefundMothers(testctx)
	if !ms[0].Enabled {
		t.Fatal("successful team entry did not reset")
	}
	finishSafeCleanup(t, s, next.ID)
	resourceSQL(t, s, "UPDATE refund_resource_tasks SET state='done'")
	ms[0].AccessToken = "replacement-fixture"
	if _, e := s.SaveRefundMother(testctx, a, ms[0]); e != nil {
		t.Fatal(e)
	}
	tx, e := s.Pool.Begin(testctx)
	if e != nil {
		t.Fatal(e)
	}
	defer tx.Rollback(testctx)
	if e = recordMotherAccountFailure(testctx, tx, j, "login_failed"); e != nil {
		t.Fatal(e)
	}
	if e = tx.Commit(testctx); e != nil {
		t.Fatal(e)
	}
	ms, _ = s.RefundMothers(testctx)
	if !ms[0].Enabled {
		t.Fatal("stale failure disabled replacement credentials")
	}
}

func terminalOrder(t *testing.T, s *Store, u, email, status string) (string, string) {
	t.Helper()
	key := ID()
	ids, _, e := s.CreateRefunds(testctx, u, key, []refundprovider.Account{account(email)}, 1000)
	if e != nil {
		t.Fatal(e)
	}
	if e = s.Transition(testctx, u, ids[0], "processing", ""); e != nil {
		t.Fatal(e)
	}
	if e = s.Transition(testctx, u, ids[0], status, ""); e != nil {
		t.Fatal(e)
	}
	if e = s.Transition(testctx, u, ids[0], status, ""); e != nil {
		t.Fatal(e)
	}
	return ids[0], key
}
func TestAccountFailurePolicyFourTerminalOrdersGlobalAndAtomic(t *testing.T) {
	s, a, u := fixture(t)
	var id, key string
	for i := 1; i <= 4; i++ {
		owner := u
		if i%2 == 0 {
			owner = a
		}
		id, key = terminalOrder(t, s, owner, "REPEAT@fixture.invalid", "failed")
		var count int
		s.Pool.QueryRow(testctx, "SELECT failure_count FROM refund_account_restrictions WHERE email='repeat@fixture.invalid'").Scan(&count)
		if count != i {
			t.Fatal("internal repeat changed order count", count, i)
		}
	}
	ids, replay, e := s.CreateRefunds(testctx, a, key, []refundprovider.Account{account("REPEAT@fixture.invalid")}, 1000)
	if e != nil || !replay || ids[0] != id {
		t.Fatal("idempotent replay rejected", e)
	}
	var wg sync.WaitGroup
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, _, e := s.CreateRefunds(testctx, u, ID(), []refundprovider.Account{account("other@fixture.invalid"), account("repeat@fixture.invalid")}, 1000)
			if !errors.Is(e, ErrAccountFailuresRestricted) {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	var orders int
	s.Pool.QueryRow(testctx, "SELECT count(*) FROM refunds").Scan(&orders)
	if orders != 4 {
		t.Fatal("rejected batch partially accepted", orders)
	}
	balance(t, s, u, 10000, 0)
	balance(t, s, a, 100000, 0)
	resourceSQL(t, s, "UPDATE refund_account_restrictions SET cleared_at=clock_timestamp(),failure_count=0")
	terminalOrder(t, s, u, "repeat@fixture.invalid", "failed")
	var count int
	s.Pool.QueryRow(testctx, "SELECT failure_count FROM refund_account_restrictions").Scan(&count)
	if count != 1 {
		t.Fatal("clear not effective", count)
	}
	terminalOrder(t, s, u, "unrelated@fixture.invalid", "failed") // StarBridge user remains usable.
}

func TestAccountFailurePolicySuccessAndDisabledThreshold(t *testing.T) {
	s, _, u := fixture(t)
	for i := 0; i < 3; i++ {
		terminalOrder(t, s, u, "reset@fixture.invalid", "failed")
	}
	terminalOrder(t, s, u, "reset@fixture.invalid", "succeeded")
	var cleared int
	s.Pool.QueryRow(testctx, "SELECT failure_count FROM refund_account_restrictions WHERE email='reset@fixture.invalid'").Scan(&cleared)
	if cleared != 0 {
		t.Fatal("success did not clear target streak", cleared)
	}
	terminalOrder(t, s, u, "next@fixture.invalid", "banned")
	var count int
	s.Pool.QueryRow(testctx, "SELECT failure_count FROM refund_account_restrictions WHERE email='next@fixture.invalid'").Scan(&count)
	if count != 1 {
		t.Fatal("banned outcome did not count", count)
	}
	resourceSQL(t, s, `UPDATE refund_settings SET resource_recovery=resource_recovery||'{"account_failure_limit":0}'`)
	for i := 0; i < 5; i++ {
		terminalOrder(t, s, u, "next@fixture.invalid", "failed")
	}
	balance(t, s, u, 9000, 0)
}

func TestMotherSessionEncryptedRoundTripAndPlainATReplacement(t *testing.T) {
	s, a, _ := fixture(t)
	configure(t, s, a, 1)
	ms, _ := s.RefundMothers(testctx)
	m := ms[0]
	rawClaims, _ := json.Marshal(map[string]any{"email": m.Email, "https://api.openai.com/auth": map[string]any{"chatgpt_account_id": m.TeamID}})
	at := "e30." + base64.RawURLEncoding.EncodeToString(rawClaims) + ".fixture"
	raw, _ := json.Marshal(map[string]any{"accessToken": at, "sessionToken": "fixture-private-session", "WARNING_BANNER": strings.Repeat("fixture ", 4800)})
	m.Session = string(raw)
	if _, e := s.SaveRefundMother(testctx, a, m); e != nil {
		t.Fatal(e)
	}
	var secret []byte
	if e := s.Pool.QueryRow(testctx, "SELECT session_secret FROM refund_mothers WHERE id=$1", m.ID).Scan(&secret); e != nil {
		t.Fatal(e)
	}
	if strings.Contains(string(secret), "fixture-private-session") {
		t.Fatal("stored plaintext session")
	}
	var saved string
	e := s.openSecret("mother-session:"+m.ID, secret, &saved)
	if e != nil || saved != string(raw) {
		t.Fatal("full session lost", e)
	}
	ms, _ = s.RefundMothers(testctx)
	public, _ := json.Marshal(ms)
	if strings.Contains(string(public), at) || strings.Contains(string(public), "fixture-private-session") {
		t.Fatal("list leaked credential")
	}
	m = ms[0]
	m.Name = "Renamed"
	if _, e := s.SaveRefundMother(testctx, a, m); e != nil {
		t.Fatal(e)
	}
	var kept []byte
	s.Pool.QueryRow(testctx, "SELECT session_secret FROM refund_mothers WHERE id=$1", m.ID).Scan(&kept)
	if string(kept) != string(secret) {
		t.Fatal("rename overwrote session")
	}
	m.Session = at
	if _, e := s.SaveRefundMother(testctx, a, m); e != nil {
		t.Fatal(e)
	}
	var empty bool
	s.Pool.QueryRow(testctx, "SELECT session_secret IS NULL FROM refund_mothers WHERE id=$1", m.ID).Scan(&empty)
	if !empty {
		t.Fatal("plain AT left stale session")
	}
}
