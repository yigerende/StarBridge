package store

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"github.com/jackc/pgx/v5"
	"llm-refund/internal/refundprovider"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sync"
	"sync/atomic"
	"testing"
)

var testctx = context.Background()

func fixture(t *testing.T) (*Store, string, string) {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("isolated TEST_DATABASE_URL required")
	}
	connection, err := pgx.Connect(testctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	schema := "refund_test_" + ID()
	_, err = connection.Exec(testctx, "CREATE SCHEMA "+schema)
	if err != nil {
		t.Fatal(err)
	}
	cfg, err := url.Parse(dsn)
	if err != nil {
		t.Fatal(err)
	}
	params := cfg.Query()
	params.Set("search_path", schema)
	cfg.RawQuery = params.Encode()
	s, err := Open(testctx, cfg.String(), bytes.Repeat([]byte{7}, 32))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		s.Pool.Close()
		connection.Exec(testctx, "DROP SCHEMA "+schema+" CASCADE")
		connection.Close(testctx)
	})
	for _, name := range []string{"schema.sql", "refund_schema.sql", "refund_mother_schema.sql"} {
		raw, e := os.ReadFile(filepath.Join("../../../../backend", name))
		if e != nil {
			t.Fatal(e)
		}
		if _, e = s.Pool.Exec(testctx, string(raw)); e != nil {
			t.Fatal(e)
		}
	}
	admin, user := ID(), ID()
	_, err = s.Pool.Exec(testctx, "INSERT INTO users(id,email,name,password_hash,role,invite_code,balance) VALUES($1,'admin@test.invalid','Admin','fixture','admin',$1,100000),($2,'user@test.invalid','User','fixture','member',$2,10000)", admin, user)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Pool.Exec(testctx, "UPDATE refund_settings SET accepting=true")
	if err != nil {
		t.Fatal(err)
	}
	return s, admin, user
}

func TestAllSchedulerSettingsRoundTripAndDeletedResourceReservation(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	cfg, err := s.RefundSettings(testctx)
	if err != nil {
		t.Fatal(err)
	}
	cfg.MaxParallel = 3
	cfg.LoginAttempts = 3
	cfg.RetrySeconds = 45
	cfg.MotherIntervalSeconds = 120
	cfg.MotherFailover = &MotherFailoverSettings{Enabled: true, MaxSwitches: 4, RetrySeconds: 35}
	cfg.Recovery = &RefundRecoverySettings{Enabled: true, MaxRetries: 5, VerifyIntervals: []int{30, 90, 180}, CleanupIntervals: []int{15, 45, 120}, AlertSeconds: 900, CleanupFailed: true, VerificationHTTPStatuses: []int{404, 409}, PlanFallback: &PlanFallbackSettings{Enabled: true, MaxCycles: 3, MaxAttempts: 4, RetrySeconds: 40}}
	if err = s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	got, err := s.RefundSettings(testctx)
	if err != nil || !reflect.DeepEqual(got, cfg) {
		t.Fatalf("scheduler configuration lost fields: %v", err)
	}
	if err = s.SaveRefundSettings(testctx, u, cfg); !errors.Is(err, ErrForbidden) {
		t.Fatal("member changed scheduler", err)
	}
	_, _, err = s.CreateRefunds(testctx, u, "resource-retirement", []refundprovider.Account{account("active@test.invalid"), account("waiting@test.invalid")}, 1000)
	if err != nil {
		t.Fatal(err)
	}
	j, err := s.ClaimRefund(testctx)
	if err != nil || j == nil {
		t.Fatal(err)
	}
	if err = s.DeleteRefundResource(testctx, a, "mother", j.Mother.ID); err != nil {
		t.Fatal(err)
	}
	rows, err := s.RefundMothers(testctx)
	if err != nil || len(rows) != 0 {
		t.Fatal("deleted resource still in configuration", err)
	}
	e, err := s.RefundExecution(testctx, j.ID)
	if err != nil || e.Released {
		t.Fatal("deletion released active reservation", err)
	}
	// A newly added record for the same team must share the old team's occupation.
	_, err = s.SaveRefundMother(testctx, a, RefundMother{Name: "Re-added team", Email: j.Mother.Email, TeamID: j.Mother.TeamID, ProxyID: cfg.ChildProxyID, SeatType: "default", AccessToken: "replacement-offline-token", Enabled: true})
	if err != nil {
		t.Fatal(err)
	}
	next, err := s.ClaimRefund(testctx)
	if err != nil || next != nil {
		t.Fatal("same team allocated while old record is reserved", err)
	}
	balance(t, s, u, 10000, 2000)
	for _, method := range []string{"inviting", "invited", "login", "merge_v2_ready", "merge_v2_submitting", "merge_v2_accepted", "removing", "completed"} {
		checkpoint(t, s, j, method)
	}
	balance(t, s, u, 9000, 1000)
	next, err = s.ClaimRefund(testctx)
	if err != nil || next != nil {
		t.Fatal("same-team completion cooldown bypassed", err)
	}
}
func account(email string) refundprovider.Account {
	return refundprovider.Account{Email: email, Password: "offline-fixture-password", TOTPSecret: "JBSWY3DPEHPK3PXP"}
}
func balance(t *testing.T, s *Store, u string, b, f int64) {
	t.Helper()
	var got, held int64
	if err := s.Pool.QueryRow(testctx, "SELECT balance,frozen FROM users WHERE id=$1", u).Scan(&got, &held); err != nil {
		t.Fatal(err)
	}
	if got != b || held != f {
		t.Fatalf("balance/frozen: %d/%d want %d/%d", got, held, b, f)
	}
}
func TestUnifiedWalletConcurrentIdempotencyAndIsolation(t *testing.T) {
	s, a, u := fixture(t)
	var wg sync.WaitGroup
	ids := make(chan string, 24)
	for i := 0; i < 24; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			rows, _, err := s.CreateRefunds(testctx, u, "same-request", []refundprovider.Account{account("one@test.invalid")}, 1700)
			if err != nil {
				t.Error(err)
				return
			}
			ids <- rows[0]
		}()
	}
	wg.Wait()
	close(ids)
	id := ""
	for got := range ids {
		if id != "" && got != id {
			t.Fatal("duplicate order")
		}
		id = got
	}
	balance(t, s, u, 10000, 1700)
	var ledgerCount int
	s.Pool.QueryRow(testctx, "SELECT count(*) FROM ledger WHERE business='refund'").Scan(&ledgerCount)
	if ledgerCount != 1 {
		t.Fatal("duplicate freeze", ledgerCount)
	}
	if _, err := s.Refund(testctx, a, id); !errors.Is(err, ErrNotFound) {
		t.Fatal("cross-user detail", err)
	}
	if _, _, err := s.CreateRefunds(testctx, u, "same-request", []refundprovider.Account{account("other@test.invalid")}, 1700); !errors.Is(err, ErrConflict) {
		t.Fatal("idempotency mismatch", err)
	}
	if _, _, err := s.CreateRefunds(testctx, a, "another-owner", []refundprovider.Account{account("one@test.invalid")}, 1700); !errors.Is(err, ErrDuplicate) {
		t.Fatal("cross-user duplicate", err)
	}
	if _, _, err := s.CreateRefunds(testctx, u, "atomic-batch", []refundprovider.Account{account("new@test.invalid"), account("one@test.invalid")}, 1700); !errors.Is(err, ErrDuplicate) {
		t.Fatal(err)
	}
	balance(t, s, u, 10000, 1700)
	var plaintext []byte
	s.Pool.QueryRow(testctx, "SELECT credentials FROM refunds WHERE id=$1", id).Scan(&plaintext)
	if bytes.Contains(plaintext, []byte("offline-fixture")) {
		t.Fatal("unencrypted credentials")
	}
	s.Pool.Exec(testctx, "UPDATE refund_settings SET accepting=false")
	if _, replay, err := s.CreateRefunds(testctx, u, "same-request", []refundprovider.Account{account("one@test.invalid")}, 9999); err != nil || !replay {
		t.Fatal("replay must retain original quote", err)
	}
	for i := 0; i < 24; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := s.Transition(testctx, u, id, "cancelled", ""); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	balance(t, s, u, 10000, 0)
	if err := s.Transition(testctx, u, id, "succeeded", ""); !errors.Is(err, ErrState) {
		t.Fatal("late success after cancellation", err)
	}
}
func TestConcurrentOverspendAndBatchRollback(t *testing.T) {
	s, _, u := fixture(t)
	var successes atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 24; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_, _, e := s.CreateRefunds(testctx, u, fmt.Sprintf("separate-%d", i), []refundprovider.Account{account(fmt.Sprintf("c%d@test.invalid", i))}, 3000)
			if e == nil {
				successes.Add(1)
			} else if !errors.Is(e, ErrBalance) {
				t.Error(e)
			}
		}(i)
	}
	wg.Wait()
	if successes.Load() != 3 {
		t.Fatal(successes.Load())
	}
	balance(t, s, u, 10000, 9000)
}
func configure(t *testing.T, s *Store, a string, n int) {
	t.Helper()
	p, err := s.SaveRefundProxy(testctx, a, RefundProxy{Name: "Offline proxy", URL: "http://proxy.invalid:8080", Enabled: true})
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < n; i++ {
		_, err = s.SaveRefundMother(testctx, a, RefundMother{Name: fmt.Sprint("Resource", i), Email: fmt.Sprintf("mother%d@test.invalid", i), TeamID: fmt.Sprint("offline-team-", i), ProxyID: p, SeatType: "default", AccessToken: "offline-fixture-token", Enabled: true})
		if err != nil {
			t.Fatal(err)
		}
	}
	if err = s.SaveRefundSettings(testctx, a, RefundSettings{Enabled: true, MaxParallel: 2, LoginAttempts: 2, RetrySeconds: 5, ChildProxyID: p}); err != nil {
		t.Fatal(err)
	}
}
func checkpoint(t *testing.T, s *Store, j *RefundJob, method string) {
	t.Helper()
	if err := s.RefundCheckpoint(testctx, j, Checkpoint{Method: method, TeamID: j.Mother.TeamID, UserID: "offline-child"}); err != nil {
		t.Fatalf("%s: %v", method, err)
	}
}
func TestIndependentQueueSerialResourceAndOneSettlement(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 2)
	_, _, err := s.CreateRefunds(testctx, u, "four-orders", []refundprovider.Account{account("a@test.invalid"), account("b@test.invalid"), account("c@test.invalid"), account("d@test.invalid")}, 1000)
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var jobs []*RefundJob
	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
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
	if len(jobs) != 2 || jobs[0].Mother.ID == jobs[1].Mother.ID {
		t.Fatalf("resource exclusion failed: %d jobs", len(jobs))
	}
	j := jobs[0]
	for _, m := range []string{"inviting", "invited", "login", "merge_v2_ready", "merge_v2_submitting", "merge_v2_accepted", "removing", "completed"} {
		checkpoint(t, s, j, m)
	}
	balance(t, s, u, 9000, 3000)
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			e := s.RefundCheckpoint(testctx, j, Checkpoint{Method: "completed", TeamID: j.Mother.TeamID, UserID: "offline-child"})
			if e != nil && !errors.Is(e, ErrState) {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	balance(t, s, u, 9000, 3000)
	var n int
	s.Pool.QueryRow(testctx, "SELECT count(*) FROM ledger WHERE refund_order_id=$1 AND kind='charge'", j.ID).Scan(&n)
	if n != 1 {
		t.Fatal("duplicate settlement", n)
	}
	// A stale lease cannot make progress or settle another execution attempt.
	stale := *jobs[1]
	stale.LeaseToken = "stale"
	if e := s.RefundCheckpoint(testctx, &stale, Checkpoint{Method: "inviting", TeamID: stale.Mother.TeamID}); !errors.Is(e, ErrState) {
		t.Fatal("stale lease", e)
	}
}
func TestUnknownSubmissionRetainsFreezeAndDoesNotReplay(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	_, _, err := s.CreateRefunds(testctx, u, "unknown-submit", []refundprovider.Account{account("unknown@test.invalid")}, 1200)
	if err != nil {
		t.Fatal(err)
	}
	j, err := s.ClaimRefund(testctx)
	if err != nil || j == nil {
		t.Fatal(err)
	}
	for _, m := range []string{"inviting", "invited", "login", "merge_v2_ready", "merge_v2_submitting"} {
		checkpoint(t, s, j, m)
	}
	if err = s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "network_error", HTTPStatus: 503}); err != nil {
		t.Fatal(err)
	}
	balance(t, s, u, 10000, 1200)
	e, err := s.RefundExecution(testctx, j.ID)
	if err != nil || e.Released {
		t.Fatal("unknown released", err)
	}
	var step string
	s.Pool.QueryRow(testctx, "SELECT step FROM refund_execution WHERE refund_id=$1", j.ID).Scan(&step)
	if step != "submitting" {
		t.Fatal("unknown replayed", step)
	}
}
