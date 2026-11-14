package store

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestFallbackWaitSettingsClaimAndInitialization(t *testing.T) {
	s, admin, user := fixture(t)
	configure(t, s, admin, 1)
	cfg, err := s.RefundSettings(testctx)
	if err != nil || cfg.ResourceRecovery.FallbackPlanWaitSeconds != 5 {
		t.Fatal(cfg.ResourceRecovery, err)
	}
	for _, seconds := range []int{-1, 61} {
		cfg.ResourceRecovery.FallbackPlanWaitSeconds = seconds
		if s.SaveRefundSettings(testctx, admin, cfg) == nil {
			t.Fatal("invalid wait accepted")
		}
	}
	for _, seconds := range []int{0, 60, 7} {
		cfg.ResourceRecovery.FallbackPlanWaitSeconds = seconds
		if err = s.SaveRefundSettings(testctx, admin, cfg); err != nil {
			t.Fatal(err)
		}
		saved, e := s.RefundSettings(testctx)
		if e != nil || saved.ResourceRecovery.FallbackPlanWaitSeconds != seconds {
			t.Fatal(saved, e)
		}
	}
	j := resourceJob(t, s, user, "plan@fixture.invalid")
	if j.Settings.ResourceRecovery.FallbackPlanWaitSeconds != 7 {
		t.Fatal("claim lost wait")
	}
	raw, err := os.ReadFile("../../../../backend/refund_schema.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.Pool.Exec(testctx, string(raw)); err != nil {
		t.Fatal(err)
	}
	cfg, err = s.RefundSettings(testctx)
	if err != nil || cfg.ResourceRecovery.FallbackPlanWaitSeconds != 7 {
		t.Fatal("saved wait overwritten", err)
	}
	if _, err = s.Pool.Exec(testctx, "UPDATE refund_settings SET resource_recovery=resource_recovery-'fallback_plan_wait_seconds'"); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		if _, err = s.Pool.Exec(testctx, string(raw)); err != nil {
			t.Fatal(err)
		}
	}
	cfg, err = s.RefundSettings(testctx)
	if err != nil || cfg.ResourceRecovery.FallbackPlanWaitSeconds != 5 {
		t.Fatal("default not initialized", err)
	}
}

func TestFallbackEvidenceStoredWithTypedPrivateFacts(t *testing.T) {
	s, admin, user := fixture(t)
	configure(t, s, admin, 1)
	j := resourceJob(t, s, user, "evidence@fixture.invalid")
	var e RefundEvidence
	err := json.Unmarshal([]byte(`{"stage":"fallback_plan","side":"child","event":"response","plan_check":{"phase":"accounts","result":"rejected","reason":"personal_account_mismatch","fields":{"token.account_id":"personal","account.account_id":"other","account.structure":"workspace","entitlement.subscription_plan":"chatgptpromax","password":"SECRET","session.account_id":"https://SECRET"},"types":{"account.structure":"string","entitlement.plan_type":"missing","access_token":"string"},"checks":{"account_id_matches":false,"entitlement.has_active_subscription":false,"password":true},"account_keys":["personal","default","https://SECRET"]}}`), &e)
	if err != nil {
		t.Fatal(err)
	}
	if err = s.AppendRefundDiagnostics(testctx, j, []RefundDiagnostic{{Evidence: &e, HTTPStatus: 200}}); err != nil {
		t.Fatal(err)
	}
	var raw string
	if err = s.Pool.QueryRow(testctx, "SELECT diagnostic::text FROM refund_execution_diagnostics WHERE refund_id=$1 ORDER BY id DESC LIMIT 1", j.ID).Scan(&raw); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"personal_account_mismatch", "chatgptpromax", "account_id_matches", "false", "missing"} {
		if !strings.Contains(raw, key) {
			t.Fatal("lost evidence", key)
		}
	}
	for _, key := range []string{"SECRET", "password", "access_token"} {
		if strings.Contains(raw, key) {
			t.Fatal("leaked field", key)
		}
	}
	e.Stage = "login"
	if e.Sanitized().PlanCheck != nil {
		t.Fatal("wrong stage accepted")
	}
	e.Stage = "fallback_plan"
	e.Side = "mother"
	if e.Sanitized().PlanCheck != nil {
		t.Fatal("wrong side accepted")
	}
}
