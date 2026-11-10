package store

import (
	"context"
	"encoding/json"

	"github.com/jackc/pgx/v5"
)

// Only used after confirmed cleanup of an unsubmitted login attempt. Read the
// durable failure that started cleanup: later cleanup errors must not replace
// its cause, and an earlier login attempt must not label the current result.
func confirmedLoginFailureMessage(ctx context.Context, tx pgx.Tx, id string) (string, error) {
	var raw []byte
	err := tx.QueryRow(ctx, `WITH login AS (
 SELECT id,created_at FROM refund_events WHERE refund_id=$1 AND internal
 AND message='正在使用密码和 2FA 登录受邀团队。' ORDER BY id DESC LIMIT 1
), cleanup AS (
 SELECT e.created_at FROM refund_events e,login l WHERE e.refund_id=$1 AND e.internal
 AND e.id>l.id AND e.message='已安排资源清理及结果核对。' ORDER BY e.id LIMIT 1
)
SELECT d.diagnostic FROM refund_execution_diagnostics d,login l,cleanup c
WHERE d.refund_id=$1 AND d.created_at>=l.created_at AND d.created_at<=c.created_at
AND d.diagnostic->>'code'<>'protocol_trace' ORDER BY d.id DESC LIMIT 1`, id).Scan(&raw)
	if err == pgx.ErrNoRows {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	var d RefundDiagnostic
	if json.Unmarshal(raw, &d) != nil {
		return "", nil
	}
	d = d.Sanitized()
	if d.Code == "extra_verification" {
		return verificationReleasedMessage, nil
	}
	if d.Code == "account_unavailable" && d.Evidence != nil && d.Evidence.Side == "child" &&
		(d.Evidence.Reason == "account_deactivated" || d.Evidence.UpstreamCode == "account_deactivated") {
		return deactivatedReleasedMessage, nil
	}
	return "", nil
}
