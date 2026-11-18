package store

import (
	"os"
	"strings"
	"testing"
	"time"
)

func TestPasswordFallbackSettingsPersistAndClaim(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	cfg, err := s.RefundSettings(testctx)
	if err != nil || !cfg.ResourceRecovery.EmailPasswordFallback {
		t.Fatal("missing default", err)
	}
	cfg.ResourceRecovery.EmailPasswordFallback = false
	if err = s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	j := resourceJob(t, s, u, "password@fixture.invalid")
	if j.Settings.ResourceRecovery.EmailPasswordFallback {
		t.Fatal("claim lost disabled setting")
	}
	raw, err := os.ReadFile("../../../../backend/refund_schema.sql")
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		resourceSQL(t, s, string(raw))
	}
	cfg, err = s.RefundSettings(testctx)
	if err != nil || cfg.ResourceRecovery.EmailPasswordFallback {
		t.Fatal("saved false overwritten", err)
	}
	resourceSQL(t, s, "UPDATE refund_settings SET resource_recovery=resource_recovery-'email_password_fallback'")
	for i := 0; i < 2; i++ {
		resourceSQL(t, s, string(raw))
	}
	cfg, err = s.RefundSettings(testctx)
	if err != nil || !cfg.ResourceRecovery.EmailPasswordFallback {
		t.Fatal("default not initialized", err)
	}
}

func TestPasswordFallbackTimelineKeepsAttemptAndLaterFailure(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	j := resourceJob(t, s, u, "timeline-password@fixture.invalid")
	base := time.Now().Add(-time.Minute)
	end := base.Add(10 * time.Second)
	second := base.Add(20 * time.Second)
	out := &RefundTimeline{Items: []RefundTimelineItem{
		{Stage: "登", Status: "未完成", StartedAt: &base, FinishedAt: &end},
		{Stage: "登", Status: "已完成", StartedAt: &second},
	}}
	for i, e := range []RefundEvidence{
		{Stage: "password_fallback", Side: "child", Event: "start"},
		{Stage: "password_fallback", Side: "child", Event: "response"},
		{Stage: "mfa_verify", Side: "child", Event: "failure", Reason: "email_verification_after_password"},
	} {
		resourceSQL(t, s, "INSERT INTO refund_execution_diagnostics(refund_id,created_at,diagnostic) VALUES($1,$2,$3)", j.ID, base.Add(time.Duration(i+1)*time.Second), RefundDiagnostic{Code: "protocol_trace", Evidence: &e})
	}
	tx, err := s.Pool.Begin(testctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(testctx)
	if err = readTimelineFailures(testctx, tx, j.ID, out); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.Items[0].Reason, "密码验证通过") || !strings.Contains(out.Items[0].Reason, "仍需邮箱验证码") || out.Items[1].Reason != "" {
		t.Fatal(out.Items)
	}
}

func TestPasswordFallbackAuthReadUsesExistingRecoveryAndWallet(t *testing.T) {
	testLoginConflictRecoveryAndWallet(t, 503, "auth_session_read", "login_upstream_unavailable")
}

func TestPasswordFallbackConfirmedCleanupMessage(t *testing.T) {
	for _, after := range []bool{false, true} {
		s, a, u := fixture(t)
		configure(t, s, a, 1)
		j := resourceJob(t, s, u, "email-code@fixture.invalid")
		base := time.Now().Add(-time.Minute)
		resourceSQL(t, s, "INSERT INTO refund_events(refund_id,message,internal,created_at) VALUES($1,$2,true,$3)", j.ID, "正在使用密码和 2FA 登录受邀团队。", base)
		e := &RefundEvidence{Stage: "password", Side: "child", Event: "failure", Reason: "extra_verification", LoginPage: "email_otp_verification"}
		want := emailVerificationReleasedMessage
		if after {
			e.Reason = "email_verification_after_password"
			want = passwordEmailReleasedMessage
		}
		resourceSQL(t, s, "INSERT INTO refund_execution_diagnostics(refund_id,created_at,diagnostic) VALUES($1,$2,$3)", j.ID, base.Add(time.Second), RefundDiagnostic{Code: "extra_verification", Evidence: e})
		resourceSQL(t, s, "INSERT INTO refund_events(refund_id,message,internal,created_at) VALUES($1,$2,true,$3)", j.ID, "已安排资源清理及结果核对。", base.Add(2*time.Second))
		tx, err := s.Pool.Begin(testctx)
		if err != nil {
			t.Fatal(err)
		}
		got, err := confirmedLoginFailureMessage(testctx, tx, j.ID)
		tx.Rollback(testctx)
		if err != nil || got != want || !publicProgress(got) {
			t.Fatal(got, err)
		}
	}
}
