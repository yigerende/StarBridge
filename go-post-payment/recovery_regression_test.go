package main

import "testing"

func TestCancellation404RecoveryRetainsFence(t *testing.T) {
	tx, _ := fixture(t, false,
		step{"subscriptions", 200, active, ""},
		step{"/api/auth/session", 200, refreshed, ""},
		step{"subscriptions", 200, active, ""},
		step{"subscriptions/cancel", 404, `{"detail":"no active subscription found for account"}`, ""},
		step{"subscriptions", 200, active, ""})
	if code := protocol_FlowErrorCode(tx.finishPayment()); code != "cancellation_not_ready" {
		t.Fatalf("expected delayed recovery, got %s", code)
	}
	if tx.cp.Payment != "charged" || tx.cp.Activation != "active" || tx.cp.CancelAttempts != 1 {
		t.Fatal("payment or cancellation fence lost")
	}
	resumed, tr := fixture(t, false,
		step{"subscriptions", 200, active, ""},
		step{"/api/auth/session", 200, refreshed, ""},
		step{"subscriptions", 200, active, ""},
		step{"subscriptions/cancel", 200, `{}`, ""},
		step{"subscriptions", 200, off, ""})
	resumed.cp = tx.cp
	resumed.platform.CP = tx.cp
	if err := resumed.finishPayment(); err != nil {
		t.Fatal(err)
	}
	if len(tr.steps) != 0 || resumed.cp.CancelAttempts != 2 || resumed.cp.Renewal != "off" || resumed.cp.Phase != "completed" {
		t.Fatal("original cancellation was not recovered")
	}
}

func TestCancellationGeneric404IsNotSynchronizationDelay(t *testing.T) {
	for _, body := range []string{`{"detail":"Not Found"}`, `{"detail":"no active subscription found for account","detail":"Not Found"}`, `<html>Not Found</html>`} {
		tx, _ := fixture(t, false,
			step{"subscriptions", 200, active, ""}, step{"/api/auth/session", 200, refreshed, ""},
			step{"subscriptions", 200, active, ""}, step{"subscriptions/cancel", 404, body, ""}, step{"subscriptions", 200, active, ""})
		if code := protocol_FlowErrorCode(tx.finishPayment()); code != "http_404" && code != "invalid_json_response" {
			t.Fatalf("generic error became retryable: %s", code)
		}
		if tx.cp.CancelAttempts != 1 {
			t.Fatal("unexpected cancellation replay")
		}
	}
}

func TestRenewal401RefreshesEvenAfterProactiveRefresh(t *testing.T) {
	tx, tr := fixture(t, false,
		step{"subscriptions", 200, active, ""}, step{"/api/auth/session", 200, refreshed, ""},
		step{"subscriptions", 401, `{}`, ""}, step{"/api/auth/session", 200, refreshed, ""},
		step{"subscriptions", 200, active, ""}, step{"subscriptions/cancel", 200, `{}`, ""}, step{"subscriptions", 200, off, ""})
	if err := tx.finishPayment(); err != nil {
		t.Fatal(err)
	}
	if len(tr.steps) != 0 || tx.cp.CancelAttempts != 1 || tx.cp.Renewal != "off" {
		t.Fatal("refresh recovery failed")
	}
}

func TestCancellation401RefreshesWithProactiveRefreshEnabled(t *testing.T) {
	tx, tr := fixture(t, false,
		step{"subscriptions", 200, active, ""}, step{"/api/auth/session", 200, refreshed, ""},
		step{"subscriptions", 200, active, ""}, step{"subscriptions/cancel", 401, `{}`, ""},
		step{"/api/auth/session", 200, refreshed, ""}, step{"subscriptions", 200, active, ""},
		step{"subscriptions/cancel", 200, `{}`, ""}, step{"subscriptions", 200, off, ""})
	if err := tx.finishPayment(); err != nil {
		t.Fatal(err)
	}
	if len(tr.steps) != 0 || tx.cp.CancelAttempts != 2 || tx.cp.Renewal != "off" {
		t.Fatal("401 recovery failed")
	}
}

func TestCancellationAndReadTimeoutResumeWithoutAnotherWriteWhenAlreadyOff(t *testing.T) {
	tx, _ := fixture(t, false,
		step{"subscriptions", 200, active, ""}, step{"/api/auth/session", 200, refreshed, ""},
		step{"subscriptions", 200, active, ""}, step{"subscriptions/cancel", 0, "", "network_error"},
		step{"subscriptions", 0, "", "network_error"})
	if code := protocol_FlowErrorCode(tx.finishPayment()); code != "network_error" {
		t.Fatalf("wrong failure: %s", code)
	}
	if tx.cp.CancelAttempts != 1 || tx.cp.Payment != "charged" {
		t.Fatal("lost original evidence")
	}
	resumed, tr := fixture(t, false,
		step{"subscriptions", 200, off, ""}, step{"/api/auth/session", 200, refreshed, ""}, step{"subscriptions", 200, off, ""})
	resumed.cp = tx.cp
	resumed.platform.CP = tx.cp
	if err := resumed.finishPayment(); err != nil {
		t.Fatal(err)
	}
	if len(tr.steps) != 0 || resumed.cp.CancelAttempts != 1 || resumed.cp.Phase != "completed" {
		t.Fatal("cancel was replayed after recovery")
	}
}
