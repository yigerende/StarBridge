package store

import (
	"encoding/json"
	"fmt"
	"llm-refund/internal/refundprovider"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestChildSessionSupplierParameters(t *testing.T) {
	for _, tc := range []struct{ vendor, raw, user, pass string }{
		{"iproyal", "http://fixture:secret_country-us_session-old_lifetime-5m@gateway.invalid:80", "fixture", "secret_country-ph_session-12345678_lifetime-1h"},
		{"cliproxy", "socks5://fixture-region-US-sid-old-t-5:secret@gateway.invalid:80", "fixture-region-PH-sid-12345678-t-60", "secret"},
		{"1024proxy", "http://fixture-region-US-sid-old-t-5:secret@gateway.invalid:80", "fixture-region-PH-sid-12345678-t-60", "secret"},
		{"711proxy", "http://fixture-zone-custom-region-US-session-old-sessTime-5-sessAuto-1:secret@gateway.invalid:80", "fixture-zone-custom-region-PH-session-12345678-sessTime-60-sessAuto-1", "secret"},
	} {
		t.Run(tc.vendor, func(t *testing.T) {
			v := RefundProxy{Vendor: tc.vendor}
			endpoint, err := childSessionEndpoint(v, tc.raw, "12345678")
			if err != nil {
				t.Fatal(err)
			}
			parsed, _ := url.Parse(endpoint)
			pass, _ := parsed.User.Password()
			if parsed.User.Username() != tc.user || pass != tc.pass {
				t.Fatal("supplier parameters incorrect")
			}
			again, err := childSessionEndpoint(v, endpoint, "87654321")
			if err != nil || strings.Contains(again, "12345678") {
				t.Fatal("stale session retained")
			}
			if _, err = childSessionEndpoint(v, "http://gateway.invalid:80", "12345678"); err == nil {
				t.Fatal("missing credentials accepted")
			}
		})
	}
	for _, v := range []RefundProxy{{Vendor: "unknown"}, {Country: "invalid"}, {Vendor: "711proxy", SessionMinutes: 4}, {Vendor: "1024proxy", SessionMinutes: 121}} {
		if _, err := childSessionEndpoint(v, "http://u:p@gateway.invalid:80", "12345678"); err == nil {
			t.Fatal("invalid policy accepted")
		}
	}
}

func dynamicChild(t *testing.T, s *Store, a, vendor, name string) string {
	t.Helper()
	id, err := s.SaveRefundProxy(testctx, a, RefundProxy{Name: name, Vendor: vendor, Country: "ph", SessionMinutes: 60, Target: "child", Enabled: true, URL: "http://fixture:secret@gateway.invalid:8080"})
	if err != nil {
		t.Fatal(err)
	}
	return id
}

func TestChildSessionsPinRetriesFallbackAndBackup(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 2)
	cfg, _ := s.RefundSettings(testctx)
	cfg.ChildProxyID = dynamicChild(t, s, a, "iproyal", "primary")
	backup := dynamicChild(t, s, a, "711proxy", "backup")
	cfg.ChildProxyBackups = []string{backup}
	if err := s.SaveRefundSettings(testctx, a, cfg); err != nil {
		t.Fatal(err)
	}
	j := resourceJob(t, s, u, "same@fixture.invalid")
	primaryURL := j.ChildProxy
	motherURL := j.MotherProxy
	for _, step := range []string{"inviting", "invited", "login"} {
		checkpoint(t, s, j, step)
	}
	if err := s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "login_response_incomplete", Retryable: true}); err != nil {
		t.Fatal(err)
	}
	due(t, s, j.ID)
	j, err := s.ClaimRefund(testctx)
	if err != nil || j == nil || j.ChildProxy != primaryURL {
		t.Fatal("retry rotated", err)
	}
	// Concurrent final reports must cool/switch only once.
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if e := s.FinishRefundAttempt(testctx, j, childNetwork()); e != nil {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	due(t, s, j.ID)
	j, err = s.ClaimRefund(testctx)
	if err != nil || j == nil || j.ChildProxy == primaryURL || j.DiagnosticContext.ChildProxyID != backup || j.MotherProxy != motherURL {
		t.Fatal("backup binding failed", err)
	}
	backupURL := j.ChildProxy
	// Ordinary restart and resource edits cannot alter this order's saved endpoint.
	resourceSQL(t, s, "UPDATE refund_execution SET lease_token='',lease_until=NULL WHERE refund_id=$1", j.ID)
	if _, err = s.SaveRefundProxy(testctx, a, RefundProxy{ID: backup, Name: "backup", Target: "child", Enabled: true, Vendor: "711proxy", Country: "US", SessionMinutes: 30}); err != nil {
		t.Fatal(err)
	}
	resourceSQL(t, s, "UPDATE refund_child_proxy_sessions SET expires_at=now()-interval '1 second' WHERE refund_id=$1", j.ID)
	j, err = s.ClaimRefund(testctx)
	if err != nil || j == nil || j.ChildProxy != backupURL {
		t.Fatal("restart/edit/expiry rotated", err)
	}
	for _, step := range []string{"merge_v2_ready", "merge_v2_submitting"} {
		checkpoint(t, s, j, step)
	}
	if err = s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "export_uncertain", HTTPStatus: 500}); err != nil {
		t.Fatal(err)
	}
	due(t, s, j.ID)
	j, err = s.ClaimRefund(testctx)
	if err != nil || j == nil || j.RecoveryMode != "fallback" || j.ChildProxy != backupURL {
		t.Fatal("fallback lost backup", err)
	}
	for _, step := range []string{"fallback_removing", "fallback_removed", "fallback_login", "fallback_plan_started"} {
		checkpoint(t, s, j, step)
	}
	if err = s.FinishRefundAttempt(testctx, j, RefundDiagnostic{Code: "fallback_unavailable"}); err != nil {
		t.Fatal(err)
	}
	due(t, s, j.ID)
	j, err = s.ClaimRefund(testctx)
	if err != nil || j == nil || j.ChildProxy != backupURL {
		t.Fatal("fallback retry rotated", err)
	}
	checkpoint(t, s, j, "fallback_free")
	balance(t, s, u, 9000, 0)
	var count int
	var raw string
	s.Pool.QueryRow(testctx, "SELECT count(*),string_agg(encode(endpoint_secret,'hex'),',') FROM refund_child_proxy_sessions WHERE refund_id=$1", j.ID).Scan(&count, &raw)
	if count != 2 || strings.Contains(raw, "fixture") {
		t.Fatal("invalid binding history")
	}
	// A later order (even the same target email) has an independent session.
	// Admission policy is separate; use a private transaction to exercise binding.
	nextID := ID()
	resourceSQL(t, s, "INSERT INTO refunds(id,user_id,email,credentials,fee) SELECT $2,user_id,email,credentials,0 FROM refunds WHERE id=$1", j.ID, nextID)
	tx, err := s.Pool.Begin(testctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(testctx)
	next := &RefundJob{ID: nextID}
	if err = s.bindChildSession(testctx, tx, next, backup, false); err != nil {
		t.Fatal(err)
	}
	if next.ChildProxy == backupURL {
		t.Fatal("new order reused prior session")
	}
	if err = tx.Commit(testctx); err != nil {
		t.Fatal(err)
	}
}

func TestChildSessionsConcurrentClaimsAndSameProviderRecovery(t *testing.T) {
	for _, vendor := range []string{"iproyal", "cliproxy", "1024proxy", "711proxy"} {
		t.Run(vendor, func(t *testing.T) {
			s, a, u := fixture(t)
			configure(t, s, a, 2)
			cfg, _ := s.RefundSettings(testctx)
			cfg.ChildProxyID = dynamicChild(t, s, a, vendor, "only")
			cfg.ChildProxyBackups = []string{}
			if err := s.SaveRefundSettings(testctx, a, cfg); err != nil {
				t.Fatal(err)
			}
			if _, _, err := s.CreateRefunds(testctx, u, "concurrent", []refundprovider.Account{account("one@fixture.invalid"), account("two@fixture.invalid")}, 1000); err != nil {
				t.Fatal(err)
			}
			ch := make(chan *RefundJob, 32)
			var wg sync.WaitGroup
			for i := 0; i < 32; i++ {
				wg.Add(1)
				go func() {
					defer wg.Done()
					j, e := s.ClaimRefund(testctx)
					if e != nil {
						t.Error(e)
					}
					if j != nil {
						ch <- j
					}
				}()
			}
			wg.Wait()
			close(ch)
			if len(ch) != 2 {
				t.Fatal("claims", len(ch))
			}
			first, second := <-ch, <-ch
			if first.ChildProxy == second.ChildProxy {
				t.Fatal("shared child session")
			}
			for _, step := range []string{"inviting", "invited", "login"} {
				checkpoint(t, s, first, step)
			}
			if err := s.FinishRefundAttempt(testctx, first, childNetwork()); err != nil {
				t.Fatal(err)
			}
			due(t, s, first.ID)
			resourceSQL(t, s, "UPDATE refund_proxies SET failure_at=NULL,failure_until=NULL WHERE id=$1", cfg.ChildProxyID)
			next, err := s.ClaimRefund(testctx)
			if err != nil || next == nil || next.ChildProxy == first.ChildProxy {
				t.Fatal("recovered route reused broken session", err)
			}
			resourceSQL(t, s, "UPDATE refund_execution SET lease_token='',lease_until=NULL WHERE refund_id=$1", next.ID)
			resumed, err := s.ClaimRefund(testctx)
			if err != nil || resumed == nil || resumed.ChildProxy != next.ChildProxy {
				t.Fatal("new binding not persisted", err)
			}
			probe, err := s.PrepareProxyProbe(testctx, a, RefundProxy{ID: cfg.ChildProxyID})
			if err != nil || probe == next.ChildProxy {
				t.Fatal("probe reused business session", err)
			}
			rows, _ := s.RefundProxies(testctx)
			b, _ := json.Marshal(rows)
			if strings.Contains(string(b), "gateway.invalid") || strings.Contains(string(b), "fixture:") {
				t.Fatal("leaked proxy credentials")
			}

			balance(t, s, u, 10000, 2000)
		})
	}
}

func TestTimelineShowsAttemptSpecificReasons(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	j := resourceJob(t, s, u, "timeline@fixture.invalid")
	base := time.Now().Add(-10 * time.Minute)
	var events []timelineEvent
	cases := []struct {
		stage, reason string
		status        int
	}{
		{"authorize", "network_error", 0}, {"export", "", 500}, {"fallback_login", "phone_verification_skipped", 200}, {"fallback_login", "chatgpt_rate_limited", 429},
	}
	for i, c := range cases {
		start := base.Add(time.Duration(i*30) * time.Second)
		end := start.Add(10 * time.Second)
		message := "正在使用密码和 2FA 登录受邀团队。"
		if c.stage == "export" {
			message = "正在提交导出并停用个人空间。"
		}
		events = append(events, timelineEvent{message: message, at: start, internal: true}, timelineEvent{message: "已安排移出后套餐兜底核验。", at: end, internal: true})
		event := "failure"
		if c.stage == "export" {
			event = "response"
		}
		d := RefundDiagnostic{Code: "protocol_trace", HTTPStatus: c.status, Evidence: &RefundEvidence{Stage: c.stage, Side: "child", Event: event, Reason: c.reason, Exception: "ConnectionError"}}
		resourceSQL(t, s, "INSERT INTO refund_execution_diagnostics(refund_id,created_at,diagnostic) VALUES($1,$2,$3)", j.ID, end.Add(time.Millisecond), d)
	}
	out := projectRefundTimeline("failed", base, time.Now(), nil, nil, events)
	tx, e := s.Pool.Begin(testctx)
	if e != nil {
		t.Fatal(e)
	}
	defer tx.Rollback(testctx)
	if e = readTimelineFailures(testctx, tx, j.ID, out); e != nil {
		t.Fatal(e)
	}
	text := fmt.Sprint(out.Items)
	for _, want := range []string{"网络或代理连接中断", "HTTP 500", "手机验证", "429"} {
		if !strings.Contains(text, want) {
			t.Fatal("missing reason", want, text)
		}
	}
}
