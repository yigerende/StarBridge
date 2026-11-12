package store

import (
	"os"
	"testing"
)

func TestPostLoginWaitSettingsAndClaim(t *testing.T) {
	s, admin, user := fixture(t)
	configure(t, s, admin, 1)
	cfg, err := s.RefundSettings(testctx)
	if err != nil || cfg.ResourceRecovery.PostLoginWaitSeconds != 5 {
		t.Fatal("default wait", cfg.ResourceRecovery, err)
	}
	for _, seconds := range []int{-1, 61} {
		cfg.ResourceRecovery.PostLoginWaitSeconds = seconds
		if err := s.SaveRefundSettings(testctx, admin, cfg); err == nil {
			t.Fatal("invalid wait accepted", seconds)
		}
	}
	for _, seconds := range []int{0, 60, 13} {
		cfg.ResourceRecovery.PostLoginWaitSeconds = seconds
		if err := s.SaveRefundSettings(testctx, admin, cfg); err != nil {
			t.Fatal(err)
		}
		saved, err := s.RefundSettings(testctx)
		if err != nil || saved.ResourceRecovery.PostLoginWaitSeconds != seconds {
			t.Fatal("wait did not persist", seconds, err)
		}
	}
	job := resourceJob(t, s, user, "wait@fixture.invalid")
	if job.Settings.ResourceRecovery.PostLoginWaitSeconds != 13 {
		t.Fatal("claimed job lost configured wait")
	}
}

func TestPostLoginWaitInitializationPreservesOtherSettings(t *testing.T) {
	s, admin, _ := fixture(t)
	cfg, err := s.RefundSettings(testctx)
	if err != nil {
		t.Fatal(err)
	}
	cfg.ResourceRecovery.LoginCooldownSeconds = 75
	cfg.ResourceRecovery.PostLoginWaitSeconds = 0
	if err := s.SaveRefundSettings(testctx, admin, cfg); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile("../../../../backend/refund_schema.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Pool.Exec(testctx, string(raw)); err != nil {
		t.Fatal(err)
	}
	saved, err := s.RefundSettings(testctx)
	if err != nil || saved.ResourceRecovery.PostLoginWaitSeconds != 0 || saved.ResourceRecovery.LoginCooldownSeconds != 75 {
		t.Fatal("startup replaced saved setting", saved.ResourceRecovery, err)
	}
	if _, err := s.Pool.Exec(testctx, "UPDATE refund_settings SET resource_recovery=resource_recovery-'post_login_wait_seconds'"); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		if _, err := s.Pool.Exec(testctx, string(raw)); err != nil {
			t.Fatal(err)
		}
	}
	saved, err = s.RefundSettings(testctx)
	if err != nil || saved.ResourceRecovery.PostLoginWaitSeconds != 5 || saved.ResourceRecovery.LoginCooldownSeconds != 75 {
		t.Fatal("missing setting not initialized safely", saved.ResourceRecovery, err)
	}
}
