package refundworker_test

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"llm-refund/internal/refundprovider"
	"llm-refund/internal/refundworker"
	"llm-refund/internal/store"
)

// Substitute only upstream execution. The real Worker claims, persists every
// checkpoint, releases mothers and settles the shared wallet concurrently.
type parallelAccounts struct {
	mu                            sync.Mutex
	expected                      map[string]refundprovider.Account
	activeMothers, activeAccounts map[string]bool
	sessions                      map[string]bool
	arrived, peak, completed      int
	gate                          chan struct{}
	errors                        chan error
}

func (r *parallelAccounts) Run(ctx context.Context, j *store.RefundJob, checkpoint func(store.Checkpoint) error) (err error) {
	defer func() {
		if err != nil {
			r.errors <- err
		}
	}()
	r.mu.Lock()
	account := r.expected[j.ID]
	if account.Email != j.Account.Email || account.Password != j.Account.Password || r.activeMothers[j.Mother.TeamID] || r.activeAccounts[j.Account.Email] || j.ChildProxySessionRef == "" || r.sessions[j.ChildProxySessionRef] || r.sessions[j.ChildProxy] {
		r.mu.Unlock()
		return errors.New("concurrent account, mother or proxy isolation failed")
	}
	r.activeMothers[j.Mother.TeamID], r.activeAccounts[j.Account.Email] = true, true
	r.sessions[j.ChildProxySessionRef], r.sessions[j.ChildProxy] = true, true
	if len(r.activeMothers) > r.peak {
		r.peak = len(r.activeMothers)
	}
	gate := r.gate
	r.arrived++
	if r.arrived == 3 {
		close(gate)
		r.gate, r.arrived = make(chan struct{}), 0
	}
	r.mu.Unlock()
	for _, step := range []string{"inviting", "invited", "login", "merge_v2_ready", "merge_v2_submitting", "merge_v2_accepted", "removing", "completed"} {
		if step == "login" {
			select {
			case <-gate:
			case <-ctx.Done():
				return ctx.Err()
			}
		}
		if step == "completed" {
			// Reservation becomes reusable at this checkpoint, not when Run returns.
			r.mu.Lock()
			delete(r.activeMothers, j.Mother.TeamID)
			delete(r.activeAccounts, j.Account.Email)
			r.mu.Unlock()
		}
		c := store.Checkpoint{Method: step, TeamID: j.Mother.TeamID, UserID: "child-" + j.ID}
		if err = checkpoint(c); err != nil {
			return err
		}
		if step == "merge_v2_accepted" || step == "completed" {
			if duplicate := checkpoint(c); duplicate != nil && !errors.Is(duplicate, store.ErrState) {
				return duplicate
			}
		}
	}
	r.mu.Lock()
	r.completed++
	r.mu.Unlock()
	return nil
}

func TestMultipleAccountsExecuteConcurrentlyWithMotherRotation(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("isolated TEST_DATABASE_URL required")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(context.Background())
	schema := "refund_parallel_" + store.ID()
	if _, err = conn.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
		t.Fatal(err)
	}
	defer conn.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE")
	u, err := url.Parse(dsn)
	if err != nil {
		t.Fatal(err)
	}
	q := u.Query()
	q.Set("search_path", schema)
	u.RawQuery = q.Encode()
	db, err := store.Open(ctx, u.String(), bytes.Repeat([]byte{7}, 32))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Pool.Close()
	for _, name := range []string{"schema.sql", "refund_schema.sql", "refund_mother_schema.sql"} {
		sql, err := os.ReadFile(filepath.Join("../../../../backend", name))
		if err != nil {
			t.Fatal(err)
		}
		if _, err = db.Pool.Exec(ctx, string(sql)); err != nil {
			t.Fatal(err)
		}
	}
	if _, err = db.Pool.Exec(ctx, `INSERT INTO users(id,email,name,password_hash,role,invite_code,balance) VALUES('admin','admin@fixture.invalid','Admin','fixture','admin','admin',100000),('customer','customer@fixture.invalid','Customer','fixture','member','customer',100000); UPDATE refund_settings SET accepting=true`); err != nil {
		t.Fatal(err)
	}
	proxy, err := db.SaveRefundProxy(ctx, "admin", store.RefundProxy{Name: "mother route", URL: "http://mother.invalid:8080", Enabled: true, Target: "mother"})
	if err != nil {
		t.Fatal(err)
	}
	child, err := db.SaveRefundProxy(ctx, "admin", store.RefundProxy{Name: "child route", URL: "http://fixture-region-PH-session-old-sessTime-60-sessAuto-1:fixture@gateway.invalid:8080", Enabled: true, Target: "child", Vendor: "711proxy", Country: "PH", SessionMinutes: 60})
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		_, err = db.SaveRefundMother(ctx, "admin", store.RefundMother{Name: fmt.Sprint(i), Email: fmt.Sprintf("mother%d@fixture.invalid", i), TeamID: fmt.Sprint("offline-team-", i), ProxyID: proxy, SeatType: "default", AccessToken: "fixture-at", Enabled: true})
		if err != nil {
			t.Fatal(err)
		}
	}
	cfg, err := db.RefundSettings(ctx)
	if err != nil {
		t.Fatal(err)
	}
	cfg.Enabled, cfg.MaxParallel, cfg.ChildProxyID = true, 3, child
	if err = db.SaveRefundSettings(ctx, "admin", cfg); err != nil {
		t.Fatal(err)
	}
	accounts := []refundprovider.Account{}
	for i := 0; i < 12; i++ {
		accounts = append(accounts, refundprovider.Account{Email: fmt.Sprintf("child%d@fixture.invalid", i), Password: fmt.Sprint("fixture-password-", i), TOTPSecret: "JBSWY3DPEHPK3PXP"})
	}
	ids, _, err := db.CreateRefunds(ctx, "customer", store.ID(), accounts, 100)
	if err != nil {
		t.Fatal(err)
	}
	runner := &parallelAccounts{expected: map[string]refundprovider.Account{}, activeMothers: map[string]bool{}, activeAccounts: map[string]bool{}, sessions: map[string]bool{}, gate: make(chan struct{}), errors: make(chan error, 32)}
	for i, id := range ids {
		runner.expected[id] = accounts[i]
	}
	w := refundworker.Worker{DB: db, Runner: runner}
	done := make(chan struct{})
	go func() { defer close(done); w.Run(ctx) }()
	defer func() { cancel(); <-done }()
	for {
		select {
		case err := <-runner.errors:
			t.Fatal(err)
		default:
		}
		var completed int
		if err = db.Pool.QueryRow(ctx, "SELECT count(*) FROM refunds WHERE status='succeeded'").Scan(&completed); err != nil {
			t.Fatal(err)
		}
		runner.mu.Lock()
		allCallbacksFinished := runner.completed == 12
		runner.mu.Unlock()
		if completed == 12 && allCallbacksFinished {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("parallel workflow timeout")
		case <-time.After(25 * time.Millisecond):
		}
	}
	cancel()
	<-done
	runner.mu.Lock()
	if runner.peak != 3 || runner.completed != 12 {
		t.Errorf("peak=%d completed=%d", runner.peak, runner.completed)
	}
	runner.mu.Unlock()
	var balance, frozen int64
	if err = db.Pool.QueryRow(context.Background(), "SELECT balance,frozen FROM users WHERE id='customer'").Scan(&balance, &frozen); err != nil {
		t.Fatal(err)
	}
	if balance != 98800 || frozen != 0 {
		t.Fatal("incorrect concurrent settlement", balance, frozen)
	}
	var mothers, charges, children, leased int
	if err = db.Pool.QueryRow(context.Background(), `SELECT (SELECT count(DISTINCT mother_id) FROM refund_execution),(SELECT count(*) FROM ledger WHERE kind='charge'),(SELECT count(DISTINCT child_user_id) FROM refund_execution),(SELECT count(*) FROM refund_execution WHERE NOT released)`).Scan(&mothers, &charges, &children, &leased); err != nil {
		t.Fatal(err)
	}
	if mothers != 3 || charges != 12 || children != 12 || leased != 0 {
		t.Fatal("incorrect final isolation or duplicate settlement", mothers, charges, children, leased)
	}
}
