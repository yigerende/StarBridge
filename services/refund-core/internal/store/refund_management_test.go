package store

import (
	"llm-refund/internal/refundprovider"
	"testing"
)

func TestManagementReservationsExcludeRefundAllocation(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	ms, err := s.RefundMothers(testctx)
	if err != nil || len(ms) != 1 {
		t.Fatal(err)
	}
	m := ms[0]
	if _, _, err = s.CreateRefunds(testctx, u, "management-reservation", []refundprovider.Account{account("held@test.invalid")}, 1000); err != nil {
		t.Fatal(err)
	}
	assertBlocked := func() {
		t.Helper()
		job, e := s.ClaimRefund(testctx)
		if e != nil || job != nil {
			t.Fatal("management reservation did not block assignment", e)
		}
		balance(t, s, u, 10000, 1000)
	}
	if _, err = s.Pool.Exec(testctx, "INSERT INTO refund_mother_leases(team_id,token,expires_at) VALUES($1,'fixture',now()+interval '1 minute')", m.TeamID); err != nil {
		t.Fatal(err)
	}
	assertBlocked()
	if err = s.DeleteRefundResource(testctx, a, "mother", m.ID); err == nil {
		t.Fatal("deleted mother during management")
	}
	s.Pool.Exec(testctx, "DELETE FROM refund_mother_leases")
	if _, err = s.Pool.Exec(testctx, "INSERT INTO refund_member_leases(email,token,expires_at) VALUES('held@test.invalid','fixture',now()+interval '1 minute')"); err != nil {
		t.Fatal(err)
	}
	assertBlocked()
	s.Pool.Exec(testctx, "DELETE FROM refund_member_leases")
	job, err := s.ClaimRefund(testctx)
	if err != nil || job == nil {
		t.Fatal("completed management failed to release resource", err)
	}
}
