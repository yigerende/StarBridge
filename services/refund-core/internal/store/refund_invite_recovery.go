package store

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/jackc/pgx/v5"
)

// Private durable evidence captures the failing credential/proxy version before
// cleanup retries or administrator edits. Never trust resource keys from Python.
func recordInviteAuthorizationFailure(ctx context.Context, tx pgx.Tx, j *RefundJob, step string, d RefundDiagnostic) error {
	d = d.Sanitized()
	if step != "inviting" || d.Code != "mother_invite_auth" || d.HTTPStatus != 401 || d.Evidence == nil || d.Evidence.Side != "mother" || d.Evidence.Stage != "invite_send" || j.MotherAttemptKey == "" {
		return nil
	}
	_, err := tx.Exec(ctx, `INSERT INTO refund_execution_diagnostics(refund_id,diagnostic)
 SELECT refund_id,$2::jsonb || jsonb_build_object('failed_mother_id',$3::text,'failed_mother_attempt',$4::text)
 FROM refund_execution WHERE refund_id=$1 AND child_user_id='' AND accepted_at IS NULL
 AND terminal_code='' AND NOT verification_only AND fallback_phase=''`, j.ID, d, j.Mother.ID, j.MotherAttemptKey)
	return err
}

// Only a successful, lease-fenced cleanup checkpoint may requeue an invitation
// attempt. A 401 alone is never proof that the invitation is absent.
func finishInviteAuthorizationCleanup(ctx context.Context, tx pgx.Tx, j *RefundJob, step string) (bool, error) {
	if step != "inviting" || j.RecoveryMode != "cleanup" {
		return false, nil
	}
	var raw []byte
	var failedKey string
	err := tx.QueryRow(ctx, `SELECT d.diagnostic,d.diagnostic->>'failed_mother_attempt'
 FROM refund_execution_diagnostics d JOIN refund_execution e ON e.refund_id=d.refund_id
 WHERE d.refund_id=$1 AND d.diagnostic->>'code'='mother_invite_auth'
 AND d.diagnostic->>'http_status'='401' AND d.diagnostic->>'failed_mother_id'=e.mother_id
 AND d.diagnostic->>'failed_mother_attempt' IS NOT NULL
 AND d.created_at >= (SELECT max(created_at) FROM refund_events WHERE refund_id=$1 AND internal AND message='正在发送团队邀请。')
 AND e.child_user_id='' AND e.accepted_at IS NULL AND e.terminal_code=''
 AND NOT e.verification_only AND e.fallback_phase=''
 ORDER BY d.id DESC LIMIT 1`, j.ID).Scan(&raw, &failedKey)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return true, err
	}
	var d RefundDiagnostic
	if err = json.Unmarshal(raw, &d); err != nil {
		return true, err
	}
	// A read-only member probe cannot validate permission to send invitations.
	// Pause only the exact failed resource; new credentials/proxies remain usable.
	tag, err := tx.Exec(ctx, `UPDATE refund_mothers m SET enabled=false,updated_at=now()
 FROM refund_proxies p WHERE m.id=$1 AND p.id=m.proxy_id AND m.enabled
 AND m.id||':'||md5(m.secret)||':'||md5(p.secret)=$2`, j.Mother.ID, failedKey)
	if err != nil {
		return true, err
	}
	if tag.RowsAffected() == 1 {
		if _, err = tx.Exec(ctx, "INSERT INTO refund_events(refund_id,message,internal) VALUES($1,'母号邀请授权未通过，已暂停该母号接单；成员列表查询成功不代表邀请权限恢复。',true)", j.ID); err != nil {
			return true, err
		}
	}
	// Use current switch configuration, but retain the failed attempt's version.
	if err = tx.QueryRow(ctx, "SELECT mother_failover FROM refund_settings WHERE id").Scan(&j.Settings.MotherFailover); err != nil {
		return true, err
	}
	copyJob := *j
	copyJob.MotherAttemptKey = failedKey
	return true, queueMotherFailover(ctx, tx, &copyJob, step, d, false)
}
