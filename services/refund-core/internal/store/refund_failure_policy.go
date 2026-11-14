package store

import (
	"context"
	"github.com/jackc/pgx/v5"
)

func targetTeamFailure(d RefundDiagnostic) bool {
	return d.Evidence != nil && d.Evidence.Side == "child" && d.Evidence.Stage == "session" && d.Evidence.Reason == "team_session_not_ready" && d.HTTPStatus == 200
}

// A failed resource counts once per order and credential version. Only distinct
// target accounts since the last successful team entry/manual reset can disable it.
func recordMotherAccountFailure(ctx context.Context, tx pgx.Tx, j *RefundJob, reason string) error {
	_, err := tx.Exec(ctx, `INSERT INTO refund_mother_failure_accounts(refund_id,mother_id,attempt_key,email,reason)
 SELECT r.id,m.id,$2,lower(r.email),$3 FROM refunds r JOIN refund_execution e ON e.refund_id=r.id
 JOIN refund_mothers m ON m.id=e.mother_id JOIN refund_proxies p ON p.id=m.proxy_id
 WHERE r.id=$1 AND m.id=$4 AND (m.id||':'||md5(m.secret)||':'||md5(p.secret))=$2
 AND m.failure_reset_at<=$5
 ON CONFLICT DO NOTHING`, j.ID, j.MotherAttemptKey, reason, j.Mother.ID, j.ProxyAttemptStarted)
	if err != nil {
		return err
	}
	tag, err := tx.Exec(ctx, `UPDATE refund_mothers m SET enabled=false,auto_disabled_at=now(),auto_disabled_reason='连续多个账号无法使用此母号',updated_at=now()
 FROM refund_settings s,refund_proxies p WHERE s.id AND p.id=m.proxy_id AND m.id=$1 AND m.enabled
 AND (m.id||':'||md5(m.secret)||':'||md5(p.secret))=$2
 AND COALESCE((s.resource_recovery->>'mother_failed_accounts')::int,2)>0
 AND (SELECT count(DISTINCT email) FROM refund_mother_failure_accounts f WHERE f.mother_id=m.id AND f.attempt_key=$2 AND f.created_at>=m.failure_reset_at)>=COALESCE((s.resource_recovery->>'mother_failed_accounts')::int,2)`, j.Mother.ID, j.MotherAttemptKey)
	if err != nil {
		return err
	}
	if tag.RowsAffected() > 0 {
		_, err = tx.Exec(ctx, "INSERT INTO refund_events(refund_id,message,internal) VALUES($1,'母号连续影响多个账号，已自动停用；修复后可在母号管理重新启用。',true)", j.ID)
	}
	return err
}

// New reservations are made only after the existing cleanup checkpoint and
// delayed-member observation. A failure here never clears a submission fence.
func scheduleTargetTeamRecovery(ctx context.Context, tx pgx.Tx, j *RefundJob, step string, d RefundDiagnostic) (bool, error) {
	if j.RecoveryMode == "cleanup" || !oneOf(step, "inviting", "invited", "login") {
		return false, nil
	}
	if !(step == "login" && targetTeamFailure(d)) {
		return false, nil
	}
	var limit int
	if err := tx.QueryRow(ctx, "SELECT COALESCE((resource_recovery->>'team_session_failure_limit')::int,3) FROM refund_settings WHERE id").Scan(&limit); err != nil {
		return true, err
	}
	if limit == 0 {
		return false, nil
	}
	tag, err := tx.Exec(ctx, `INSERT INTO refund_execution_diagnostics(refund_id,diagnostic)
 SELECT refund_id,$2::jsonb||jsonb_build_object('team_session_failure',true,'failed_mother_attempt',$3::text,'failed_mother_id',$4::text)
 FROM refund_execution WHERE refund_id=$1 AND accepted_at IS NULL AND terminal_code='' AND NOT verification_only AND fallback_phase=''`, j.ID, d, j.MotherAttemptKey, j.Mother.ID)
	if err != nil {
		return true, err
	}
	if tag.RowsAffected() == 0 {
		return false, nil
	}
	var n int
	if err = tx.QueryRow(ctx, `SELECT count(*) FROM refund_execution_diagnostics WHERE refund_id=$1 AND diagnostic->>'team_session_failure'='true'
 AND diagnostic->>'failed_mother_attempt'=$2 AND created_at>=COALESCE((SELECT max(created_at) FROM refund_events WHERE refund_id=$1 AND message='正在发送团队邀请。'),'-infinity')`, j.ID, j.MotherAttemptKey).Scan(&n); err != nil {
		return true, err
	}
	// Respect the original retry budget. If it ends sooner, perform the same
	// confirmed cleanup and failover instead of stranding this account here.
	var retries int
	if err = tx.QueryRow(ctx, "SELECT retry_count FROM refund_execution WHERE refund_id=$1", j.ID).Scan(&retries); err != nil {
		return true, err
	}
	cfg := j.Settings.Recovery
	canRetry := cfg != nil && cfg.Enabled && retries < cfg.MaxRetries
	if n < limit && canRetry {
		return false, nil
	}
	if err = recordMotherAccountFailure(ctx, tx, j, d.Code); err != nil {
		return true, err
	}
	// This marker is consumed only by the existing confirmed-cleanup handler.
	tag, err = tx.Exec(ctx, `INSERT INTO refund_execution_diagnostics(refund_id,diagnostic)
 SELECT refund_id,$2::jsonb||jsonb_build_object('resource_switch_cause',true,'failed_mother_attempt',$3::text,'failed_mother_id',$4::text,'avoid_failed_mother',true)
 FROM refund_execution WHERE refund_id=$1 AND accepted_at IS NULL AND terminal_code='' AND NOT verification_only AND fallback_phase=''`, j.ID, d, j.MotherAttemptKey, j.Mother.ID)
	if err != nil {
		return true, err
	}
	if tag.RowsAffected() == 0 {
		return false, nil
	}
	if err = recordDiagnostic(ctx, tx, j.ID, d); err != nil {
		return true, err
	}
	_, err = tx.Exec(ctx, `UPDATE refund_execution SET recovery_mode='cleanup',next_attempt_at=now(),lease_token='',lease_until=NULL,
 recovery_started_at=COALESCE(recovery_started_at,now()),updated_at=now() WHERE refund_id=$1`, j.ID)
	if err == nil {
		_, err = tx.Exec(ctx, "INSERT INTO refund_events(refund_id,message,internal) VALUES($1,'未能进入目标团队，先清理并确认资源，再按换母号规则继续原单。',true)", j.ID)
	}
	return true, err
}

func resetMotherAccountFailures(ctx context.Context, tx pgx.Tx, j *RefundJob) error {
	_, err := tx.Exec(ctx, `UPDATE refund_mothers m SET failure_reset_at=now() FROM refund_proxies p
 WHERE m.id=$1 AND m.proxy_id=p.id AND (m.id||':'||md5(m.secret)||':'||md5(p.secret))=$2`, j.Mother.ID, j.MotherAttemptKey)
	return err
}
