package store

import (
	"context"
	"github.com/jackc/pgx/v5"
)

// Only transport evidence attributed to the child triggers a child exit change.
// Account failures, challenges, HTTP 429 and mother failures keep their policy.
func childProxyNetworkFailure(d RefundDiagnostic) bool {
	d = d.Sanitized()
	if d.Evidence == nil || d.Evidence.Side != "child" {
		return false
	}
	e := d.Evidence
	if e.ProxyHTTPStatus == 407 || e.ProxyHTTPStatus == 502 || e.ProxyHTTPStatus == 503 || e.ProxyHTTPStatus == 504 {
		return true
	}
	if e.Reason != "network_error" {
		return false
	}
	return d.NetworkCode != 0 || oneOf(e.Exception, "ProxyError", "ConnectionError", "TimeoutError", "ConnectTimeout", "ReadTimeout", "ConnectionResetError", "DNSError", "RequestsError", "SSLError", "OSError")
}

func recordChildProxyFailure(ctx context.Context, tx pgx.Tx, j *RefundJob, d RefundDiagnostic) error {
	if !childProxyNetworkFailure(d) {
		return nil
	}
	seconds := j.Settings.ChildProxyCooldownSeconds
	if seconds < 30 {
		seconds = 300
	}
	// The active lease fences duplicate completions. Proxy rows are never read
	// FOR UPDATE by the selector, so this cannot invert scheduler/user row locks.
	_, err := tx.Exec(ctx, `UPDATE refund_proxies SET failure_at=now(),failure_until=now()+$2*interval '1 second',
 failure_code='network',probe_until=NULL WHERE id=(SELECT child_proxy_id FROM refund_execution WHERE refund_id=$1)`, j.ID, seconds)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, "UPDATE refund_execution SET child_proxy_switch_pending=true WHERE refund_id=$1", j.ID)
	return err
}

func childCandidates(cfg RefundSettings) []string {
	result := []string{}
	seen := map[string]bool{}
	for _, id := range append([]string{cfg.ChildProxyID}, cfg.ChildProxyBackups...) {
		if id != "" && !seen[id] {
			result = append(result, id)
			seen[id] = true
		}
	}
	return result
}

// Called under the same scheduler lock as order allocation. After cooling, one
// task probes a failed exit while other tasks use the healthy alternatives.
func selectChildProxy(ctx context.Context, tx pgx.Tx, cfg RefundSettings, avoid string) (string, error) {
	ids := childCandidates(cfg)
	if len(ids) == 0 {
		return "", nil
	}
	var id string
	err := tx.QueryRow(ctx, `SELECT p.id FROM unnest($1::text[]) WITH ORDINALITY choice(id,rank)
 JOIN refund_proxies p ON p.id=choice.id WHERE p.enabled AND p.deleted_at IS NULL AND p.target IN ('both','child')
 AND (p.failure_until IS NULL OR p.failure_until<=now()) AND (p.probe_until IS NULL OR p.probe_until<=now())
 ORDER BY (p.id=$2),choice.rank LIMIT 1`, ids, avoid).Scan(&id)
	if err == pgx.ErrNoRows {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	return id, err
}

func clearChildProxyFailure(ctx context.Context, tx pgx.Tx, j *RefundJob) error {
	// Ignore success from an older concurrent attempt after a newer failure.
	_, err := tx.Exec(ctx, `UPDATE refund_proxies SET failure_at=NULL,failure_until=NULL,failure_code='',probe_until=NULL
 WHERE id=$1 AND (failure_at IS NULL OR failure_at<$2)`, j.DiagnosticContext.ChildProxyID, j.ProxyAttemptStarted)
	return err
}

func setChildProxy(ctx context.Context, tx pgx.Tx, j *RefundJob, previous, next string) error {
	_, err := tx.Exec(ctx, `UPDATE refund_execution SET child_proxy_id=$2,child_proxy_switch_pending=false,
 child_proxy_switches=child_proxy_switches+CASE WHEN child_proxy_id<>$2 THEN 1 ELSE 0 END WHERE refund_id=$1`, j.ID, next)
	if err != nil {
		return err
	}
	if previous != next {
		_, err = tx.Exec(ctx, "INSERT INTO refund_events(refund_id,message,internal) VALUES($1,'子号代理连接异常，本轮改用备用代理继续原步骤。',true)", j.ID)
	}
	return err
}
