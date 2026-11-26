package store

import (
	"bytes"
	"fmt"
	"sync"
	"testing"
	"time"

	"llm-refund/internal/refundprovider"
)

func rotationMothers(t *testing.T, s *Store) []string {
	t.Helper()
	rows, err := s.Pool.Query(testctx, "SELECT id FROM refund_mothers ORDER BY id")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var ids []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			t.Fatal(err)
		}
		ids = append(ids, id)
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	return ids
}

func assignedAt(t *testing.T, s *Store, id string) *time.Time {
	t.Helper()
	var at *time.Time
	if err := s.Pool.QueryRow(testctx, "SELECT last_assigned_at FROM refund_mothers WHERE id=$1", id).Scan(&at); err != nil {
		t.Fatal(err)
	}
	return at
}

func completeRotationJob(t *testing.T, s *Store, j *RefundJob) {
	t.Helper()
	for _, step := range []string{"inviting", "invited", "login", "merge_v2_ready", "merge_v2_submitting", "merge_v2_accepted", "removing", "completed"} {
		checkpoint(t, s, j, step)
	}
}

func TestMotherRotationSerialSurvivesRestartAndConfigurationEdits(t *testing.T) {
	s, admin, user := fixture(t)
	configure(t, s, admin, 3)
	ids := rotationMothers(t, s)
	counts := map[string]int{}
	// A separate Store models a restarted process using the same durable queue.
	restarted, err := Open(testctx, s.Pool.Config().ConnString(), bytes.Repeat([]byte{7}, 32))
	if err != nil {
		t.Fatal(err)
	}
	defer restarted.Pool.Close()
	for i := 0; i < 9; i++ {
		worker := s
		if i >= 3 {
			worker = restarted
		}
		j := resourceJob(t, worker, user, fmt.Sprintf("rotation-%d@fixture.invalid", i))
		if j.Mother.ID != ids[i%3] {
			t.Fatalf("order %d selected %s, want %s", i, j.Mother.ID, ids[i%3])
		}
		counts[j.Mother.ID]++
		at := assignedAt(t, s, j.Mother.ID)
		if at == nil {
			t.Fatal("new reservation did not persist dispatch time")
		}
		completeRotationJob(t, worker, j)
		if !assignedAt(t, s, j.Mother.ID).Equal(*at) {
			t.Fatal("completion changed rotation")
		}
		mothers, err := s.RefundMothers(testctx)
		if err != nil {
			t.Fatal(err)
		}
		for _, m := range mothers {
			if m.ID == ids[(i+1)%3] {
				m.Name += " edited"
				if _, err := s.SaveRefundMother(testctx, admin, m); err != nil {
					t.Fatal(err)
				}
			}
		}
	}
	for _, id := range ids {
		if counts[id] != 3 {
			t.Fatal("unbalanced serial allocation", counts)
		}
	}
	balance(t, s, user, 1000, 0)
}

func TestMotherRotationConcurrentClaimsRemainExclusiveAndBalanced(t *testing.T) {
	s, admin, user := fixture(t)
	configure(t, s, admin, 3)
	resourceSQL(t, s, "UPDATE refund_settings SET max_parallel=3")
	accounts := []refundprovider.Account{}
	for i := 0; i < 12; i++ {
		accounts = append(accounts, account(fmt.Sprintf("parallel-%d@fixture.invalid", i)))
	}
	if _, _, err := s.CreateRefunds(testctx, user, ID(), accounts, 100); err != nil {
		t.Fatal(err)
	}
	counts := map[string]int{}
	for round := 0; round < 4; round++ {
		jobs := make(chan *RefundJob, 24)
		var wg sync.WaitGroup
		for i := 0; i < 24; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				j, err := s.ClaimRefund(testctx)
				if err != nil {
					t.Error(err)
					return
				}
				if j != nil {
					jobs <- j
				}
			}()
		}
		wg.Wait()
		close(jobs)
		seen := map[string]bool{}
		for j := range jobs {
			if seen[j.Mother.ID] {
				t.Fatal("one mother assigned twice concurrently")
			}
			seen[j.Mother.ID] = true
			counts[j.Mother.ID]++
			completeRotationJob(t, s, j)
		}
		if len(seen) != 3 {
			t.Fatal("available capacity was not used", len(seen))
		}
	}
	for _, count := range counts {
		if count != 4 {
			t.Fatal("unbalanced concurrent allocation", counts)
		}
	}
	balance(t, s, user, 8800, 0)
}

func TestMotherRotationSkipsUnavailableAndRetainsCompletionInterval(t *testing.T) {
	for _, condition := range []string{"disabled", "expired", "auth", "blocked", "interval"} {
		t.Run(condition, func(t *testing.T) {
			s, admin, user := fixture(t)
			configure(t, s, admin, 2)
			ids := rotationMothers(t, s)
			switch condition {
			case "disabled":
				resourceSQL(t, s, "UPDATE refund_mothers SET enabled=false WHERE id=$1", ids[0])
			case "expired":
				resourceSQL(t, s, "UPDATE refund_mothers SET token_expires_at=now()-interval '1 hour' WHERE id=$1", ids[0])
			case "auth":
				resourceSQL(t, s, "UPDATE refund_mothers SET auth_failed_fingerprint=md5(secret) WHERE id=$1", ids[0])
			case "blocked":
				resourceSQL(t, s, "INSERT INTO refund_resource_health(scope,blocked,reason) SELECT team_id,true,'mother_capacity' FROM refund_mothers WHERE id=$1", ids[0])
			case "interval":
				first := resourceJob(t, s, user, "previous@fixture.invalid")
				completeRotationJob(t, s, first)
				resourceSQL(t, s, "UPDATE refund_settings SET mother_interval_seconds=120")
				// Even when oldest by dispatch time, the completion interval takes priority.
				resourceSQL(t, s, "UPDATE refund_mothers SET last_assigned_at=now()-interval '1 day' WHERE id=$1", ids[0])
			}
			before := assignedAt(t, s, ids[0])
			j := resourceJob(t, s, user, "eligible@fixture.invalid")
			if j.Mother.ID != ids[1] {
				t.Fatal("selected unavailable mother")
			}
			after := assignedAt(t, s, ids[0])
			if (before == nil) != (after == nil) || (before != nil && !before.Equal(*after)) {
				t.Fatal("ineligible mother rotation advanced")
			}
		})
	}
}

func TestMotherRotationRetryKeepsBindingAndSafeFailoverAdvancesNewMother(t *testing.T) {
	s, admin, user := fixture(t)
	configure(t, s, admin, 3)
	ids := rotationMothers(t, s)
	j := resourceJob(t, s, user, "retry@fixture.invalid")
	firstAt := assignedAt(t, s, j.Mother.ID)
	reachLogin(t, s, j)
	if err := s.FinishRefundAttempt(testctx, j, teamUnavailable()); err != nil {
		t.Fatal(err)
	}
	due(t, s, j.ID)
	retry, err := s.ClaimRefund(testctx)
	if err != nil || retry == nil || retry.ID != j.ID || retry.Mother.ID != j.Mother.ID {
		t.Fatal("ordinary retry changed reservation", err)
	}
	if !assignedAt(t, s, j.Mother.ID).Equal(*firstAt) {
		t.Fatal("retry moved rotation forward")
	}
	for _, step := range []string{"merge_v2_ready", "merge_v2_submitting", "merge_v2_accepted", "removing", "completed"} {
		checkpoint(t, s, retry, step)
	}
	next := resourceJob(t, s, user, "safe-failover@fixture.invalid")
	if next.Mother.ID != ids[1] {
		t.Fatal("retry disturbed next mother")
	}
	at := assignedAt(t, s, next.Mother.ID)
	if err := s.FinishRefundAttempt(testctx, next, RefundDiagnostic{Code: "mother_preflight_auth", HTTPStatus: 401}); err != nil {
		t.Fatal(err)
	}
	due(t, s, next.ID)
	switched, err := s.ClaimRefund(testctx)
	if err != nil || switched == nil || switched.ID != next.ID || switched.Mother.ID != ids[2] {
		t.Fatal("safe failover did not rotate", err)
	}
	if !assignedAt(t, s, next.Mother.ID).Equal(*at) || assignedAt(t, s, switched.Mother.ID) == nil {
		t.Fatal("incorrect failover dispatch history")
	}
	balance(t, s, user, 9000, 1000)
}

func TestMotherRotationClaimFailureRollsBackDispatchTime(t *testing.T) {
	s, admin, user := fixture(t)
	configure(t, s, admin, 1)
	id := rotationMothers(t, s)[0]
	var secret []byte
	if err := s.Pool.QueryRow(testctx, "SELECT secret FROM refund_mothers WHERE id=$1", id).Scan(&secret); err != nil {
		t.Fatal(err)
	}
	resourceSQL(t, s, "UPDATE refund_mothers SET secret=$2 WHERE id=$1", id, []byte("invalid ciphertext"))
	orders, _, err := s.CreateRefunds(testctx, user, ID(), []refundprovider.Account{account("rollback@fixture.invalid")}, 1000)
	if err != nil {
		t.Fatal(err)
	}
	if j, err := s.ClaimRefund(testctx); err == nil || j != nil {
		t.Fatal("invalid credential claim unexpectedly committed")
	}
	if assignedAt(t, s, id) != nil {
		t.Fatal("failed claim advanced rotation")
	}
	var status string
	if err := s.Pool.QueryRow(testctx, "SELECT status FROM refunds WHERE id=$1", orders[0]).Scan(&status); err != nil || status != "queued" {
		t.Fatal("failed claim mutated order", err, status)
	}
	balance(t, s, user, 10000, 1000)
	resourceSQL(t, s, "UPDATE refund_mothers SET secret=$2 WHERE id=$1", id, secret)
	j, err := s.ClaimRefund(testctx)
	if err != nil || j == nil || j.ID != orders[0] || assignedAt(t, s, id) == nil {
		t.Fatal("corrected claim did not persist rotation", err)
	}
}
