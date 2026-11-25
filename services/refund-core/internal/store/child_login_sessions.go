package store

import (
	"context"
	"regexp"
	"strings"

	"github.com/jackc/pgx/v5"
)

var loginSessionRef = regexp.MustCompile(`^[a-zA-Z0-9]{8}$`)
var numericLoginSessionRef = regexp.MustCompile(`^[0-9]{8}$`)

// Only the integration's typed, pre-submission login failure can renew a session.
// Upstream challenges, explicit account errors and other operations keep their policy.
func childLoginSessionFailure(step string, d RefundDiagnostic) bool {
	d = d.Sanitized()
	e := d.Evidence
	return step == "login" && d.Code == "login_proxy_session_failed" && d.Retryable && e != nil &&
		e.Side == "child" && !e.Challenge && (d.HTTPStatus == 403 || e.ProxyHTTPStatus == 403) &&
		oneOf(e.Reason, "unknown_error", "network_error", "chatgpt_signin_failed") &&
		oneOf(e.Stage, "login", "csrf", "signin", "authorize", "identifier", "auth_session_read", "password", "mfa_challenge", "mfa_verify", "workspace", "callback", "session")
}

func replaceChildLoginSession(endpoint, vendor, previous, next string) (string, error) {
	if !loginSessionRef.MatchString(previous) || !loginSessionRef.MatchString(next) || previous == next || (vendor == "711proxy" && !numericLoginSessionRef.MatchString(next)) {
		return "", ErrState
	}
	prefix := map[string]string{"iproyal": "_session-", "cliproxy": "-sid-", "1024proxy": "-sid-", "711proxy": "-session-"}[vendor]
	if prefix == "" || strings.Count(endpoint, prefix+previous) != 1 {
		return "", ErrState
	}
	return strings.Replace(endpoint, prefix+previous, prefix+next, 1), nil
}

// Caller holds the execution lock and lease fence. Renewal and retry scheduling
// commit together; a duplicated completion or ACK cannot create another session.
// Keep the saved endpoint's provider, country and duration, even after config edits.
func (s *Store) refreshChildLoginSession(ctx context.Context, tx pgx.Tx, j *RefundJob, expected, next string) (bool, error) {
	var proxyID, vendor, country, ref, endpoint string
	var generation, minutes int
	var encrypted []byte
	err := tx.QueryRow(ctx, `SELECT proxy_id,generation,endpoint_secret,vendor,country,session_minutes,session_ref
 FROM refund_child_proxy_sessions WHERE refund_id=$1 ORDER BY generation DESC LIMIT 1`, j.ID).
		Scan(&proxyID, &generation, &encrypted, &vendor, &country, &minutes, &ref)
	if err != nil {
		return false, err
	}
	if ref == "" || vendor == "other" {
		return false, nil // Static proxy URLs cannot request another sticky session.
	}
	if err = s.openSecret(childSessionLabel(j.ID, generation), encrypted, &endpoint); err != nil {
		return false, err
	}
	if expected != "" && ref != expected {
		if ref != next {
			return false, ErrState
		}
		j.ChildProxy, j.ChildProxyVendor, j.ChildProxySessionRef = endpoint, vendor, ref
		return false, nil
	}
	if next == "" {
		for tries := 0; tries < 10; tries++ {
			if next, err = childSessionRef(vendor); err != nil {
				return false, err
			}
			var used bool
			if err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM refund_child_proxy_sessions WHERE session_ref=$1)", next).Scan(&used); err != nil {
				return false, err
			}
			if !used {
				break
			}
			next = ""
		}
	}
	endpoint, err = replaceChildLoginSession(endpoint, vendor, ref, next)
	if err != nil {
		return false, err
	}
	generation++
	if encrypted, err = s.sealSecret(childSessionLabel(j.ID, generation), endpoint); err != nil {
		return false, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO refund_child_proxy_sessions(refund_id,generation,proxy_id,endpoint_secret,vendor,country,session_minutes,session_ref,expires_at)
 VALUES($1,$2,$3,$4,$5,$6,$7,$8,now()+$7::integer*interval '1 minute')`, j.ID, generation, proxyID, encrypted, vendor, country, minutes, next)
	if err != nil {
		return false, err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO refund_events(refund_id,message,internal) VALUES($1,'登录返回403，已重新生成当前子号代理会话，按原间隔重试。',true)", j.ID); err != nil {
		return false, err
	}
	j.ChildProxy, j.ChildProxyVendor, j.ChildProxySessionRef = endpoint, vendor, next
	return true, nil
}
