package store

import (
	"errors"
	"sync"
	"testing"
	"time"

	"llm-refund/internal/refundprovider"
)

func TestDeleteProxyWithReferencesPreservesRecoveryAndSettlement(t *testing.T) {
	s, admin, user := fixture(t)
	configure(t, s, admin, 1)
	s.SetRefundRuntimeAvailable(true)
	cfg, _ := s.RefundSettings(testctx)
	proxyID := cfg.ChildProxyID
	if !s.ProviderReady(testctx) {
		t.Fatal("fixture should be ready")
	}
	_, _, err := s.CreateRefunds(testctx, user, "proxy-delete", []refundprovider.Account{account("active@test.invalid"), account("waiting@test.invalid")}, 1000)
	if err != nil {
		t.Fatal(err)
	}
	job, err := s.ClaimRefund(testctx)
	if err != nil || job == nil {
		t.Fatal("claim", err)
	}
	if err = s.DeleteRefundResource(testctx, user, "proxy", proxyID); !errors.Is(err, ErrForbidden) {
		t.Fatal("member deletion", err)
	}
	// Deleting a global, mother-bound proxy with a live lease succeeds repeatedly.
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if e := s.DeleteRefundResource(testctx, admin, "proxy", proxyID); e != nil {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	proxies, err := s.RefundProxies(testctx)
	if err != nil || len(proxies) != 0 {
		t.Fatal("proxy still listed", err)
	}
	cfg, err = s.RefundSettings(testctx)
	if err != nil || cfg.ChildProxyID != "" || !cfg.Enabled {
		t.Fatal("global selection or queue setting", err)
	}
	if s.ProviderReady(testctx) {
		t.Fatal("retired proxy offered for new work")
	}
	balance(t, s, user, 10000, 2000)
	if err = s.RenewRefundLease(testctx, job); err != nil {
		t.Fatal("deletion interrupted running job", err)
	}
	if next, e := s.ClaimRefund(testctx); e != nil || next != nil {
		t.Fatal("new job used deleted proxy", e)
	}
	// A restart/retry keeps the exact proxy, even though the global choice is clear.
	_, err = s.Pool.Exec(testctx, "UPDATE refund_execution SET lease_until=NULL,lease_token='' WHERE refund_id=$1", job.ID)
	if err != nil {
		t.Fatal(err)
	}
	recovered, err := s.ClaimRefund(testctx)
	if err != nil || recovered == nil || recovered.ID != job.ID || recovered.ChildProxy != job.ChildProxy || recovered.MotherProxy != job.MotherProxy {
		t.Fatal("recovery lost original proxy", err)
	}
	for _, step := range []string{"inviting", "invited", "login", "merge_v2_ready", "merge_v2_submitting", "merge_v2_accepted", "removing", "completed"} {
		checkpoint(t, s, recovered, step)
	}
	balance(t, s, user, 9000, 1000)
	if err = s.DeleteRefundResource(testctx, admin, "proxy", proxyID); err != nil {
		t.Fatal("history blocked deletion", err)
	}
	if _, err = s.SaveRefundProxy(testctx, admin, RefundProxy{ID: proxyID, Name: "Stale editor", Enabled: true}); !errors.Is(err, ErrNotFound) {
		t.Fatal("stale edit revived deleted proxy", err)
	}
	if _, err = s.PrepareProxyProbe(testctx, admin, RefundProxy{ID: proxyID}); !errors.Is(err, ErrNotFound) {
		t.Fatal("deleted proxy can be probed", err)
	}
	if err = s.SaveResourceCheck(testctx, admin, "proxy", proxyID, time.Now(), ResourceCheck{Valid: true, CheckedAt: time.Now()}); err != nil {
		t.Fatal(err)
	}
	var checked bool
	if err = s.Pool.QueryRow(testctx, "SELECT check_json IS NOT NULL FROM refund_proxies WHERE id=$1", proxyID).Scan(&checked); err != nil || checked {
		t.Fatal("late probe restored diagnostics", err)
	}
	cfg.ChildProxyID = proxyID
	if err = s.SaveRefundSettings(testctx, admin, cfg); err == nil {
		t.Fatal("deleted global proxy selectable")
	}
	if _, err = s.SaveRefundMother(testctx, admin, job.Mother); err == nil {
		t.Fatal("deleted mother proxy selectable")
	}
	// A new proxy can be selected immediately; the queued job then proceeds.
	replacement, err := s.SaveRefundProxy(testctx, admin, RefundProxy{Name: "Replacement", URL: "http://replacement.invalid:8080", Enabled: true})
	if err != nil {
		t.Fatal(err)
	}
	job.Mother.ProxyID = replacement
	job.Mother.Enabled = true
	if _, err = s.SaveRefundMother(testctx, admin, job.Mother); err != nil {
		t.Fatal(err)
	}
	cfg.ChildProxyID = replacement
	if err = s.SaveRefundSettings(testctx, admin, cfg); err != nil {
		t.Fatal(err)
	}
	if !s.ProviderReady(testctx) {
		t.Fatal("replacement did not restore readiness")
	}
	next, err := s.ClaimRefund(testctx)
	if err != nil || next == nil || next.ID == job.ID || next.ChildProxy != "http://replacement.invalid:8080" {
		t.Fatal("replacement claim", err)
	}
	balance(t, s, user, 9000, 1000)
}

func TestDeletingOnlyMotherProxyStopsNewAssignments(t *testing.T) {
	s, admin, user := fixture(t)
	configure(t, s, admin, 1)
	cfg, _ := s.RefundSettings(testctx)
	motherProxy := cfg.ChildProxyID
	child, err := s.SaveRefundProxy(testctx, admin, RefundProxy{Name: "Child", URL: "http://child.invalid:8080", Enabled: true})
	if err != nil {
		t.Fatal(err)
	}
	cfg.ChildProxyID = child
	if err = s.SaveRefundSettings(testctx, admin, cfg); err != nil {
		t.Fatal(err)
	}
	if err = s.DeleteRefundResource(testctx, admin, "proxy", motherProxy); err != nil {
		t.Fatal(err)
	}
	_, _, err = s.CreateRefunds(testctx, user, "waiting-for-proxy", []refundprovider.Account{account("wait@test.invalid")}, 1000)
	if err != nil {
		t.Fatal(err)
	}
	if next, e := s.ClaimRefund(testctx); e != nil || next != nil {
		t.Fatal("new assignment used retired mother proxy", e)
	}
	cfg, _ = s.RefundSettings(testctx)
	if cfg.ChildProxyID != child {
		t.Fatal("unrelated global selection was cleared")
	}
	balance(t, s, user, 10000, 1000)
}
