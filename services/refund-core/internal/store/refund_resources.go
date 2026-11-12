package store

import (
	"context"
	"encoding/json"
	"github.com/jackc/pgx/v5"
	"strings"
)

type ResourceRecoverySettings struct {
	PostLoginWaitSeconds int `json:"post_login_wait_seconds"`
	FailureLimit         int `json:"failure_limit"`
	CooldownSeconds      int `json:"cooldown_seconds"`
	WatchSeconds         int `json:"watch_seconds"`
	RetrySeconds         int `json:"retry_seconds"`
	SlowRetrySeconds     int `json:"slow_retry_seconds"`
	FastAttempts         int `json:"fast_attempts"`
	LoginFailureLimit    int `json:"login_failure_limit"`
	LoginWindowSeconds   int `json:"login_window_seconds"`
	LoginCooldownSeconds int `json:"login_cooldown_seconds"`
}

func (v ResourceRecoverySettings) Validate() error {
	if v.PostLoginWaitSeconds < 0 || v.PostLoginWaitSeconds > 60 {
		return invalid("登录后等待须为 0～60 秒")
	}
	if v.FailureLimit < 1 || v.FailureLimit > 10 || v.CooldownSeconds < 30 || v.CooldownSeconds > 3600 || v.WatchSeconds < 30 || v.WatchSeconds > 1800 || v.RetrySeconds < 5 || v.RetrySeconds > 300 || v.SlowRetrySeconds < 60 || v.SlowRetrySeconds > 3600 || v.FastAttempts < 1 || v.FastAttempts > 10 || v.SlowRetrySeconds < v.RetrySeconds || v.LoginFailureLimit < 2 || v.LoginFailureLimit > 20 || v.LoginWindowSeconds < 10 || v.LoginWindowSeconds > 600 || v.LoginCooldownSeconds < 5 || v.LoginCooldownSeconds > 3600 {
		return invalid("资源恢复次数或间隔超出范围")
	}
	return nil
}

func armResource(ctx context.Context, tx pgx.Tx, j *RefundJob, email, user, state string) error {
	_, err := tx.Exec(ctx, `INSERT INTO refund_resource_tasks(id,refund_id,mother_id,team_id,email,user_id,state,watch_until)
 VALUES($1,$2,$3,$4,$5,$6,$7,now()+COALESCE((SELECT (resource_recovery->>'watch_seconds')::int FROM refund_settings WHERE id),60)*interval '1 second') ON CONFLICT(refund_id,team_id,email) DO UPDATE
 SET user_id=CASE WHEN excluded.user_id<>'' THEN excluded.user_id ELSE refund_resource_tasks.user_id END,
 state=excluded.state,updated_at=now()`, ID(), j.ID, j.Mother.ID, j.Mother.TeamID, strings.ToLower(email), user, state)
	return err
}

// Called within the same transaction that ends/detaches the execution. A failed
// order cannot abandon its membership responsibility when its fee is released.
func finishResources(ctx context.Context, tx pgx.Tx, j *RefundJob, clean, success bool, step string) error {
	// Read persisted identity: interrupted claims and administrator actions do
	// not have decrypted job credentials. Never touch a previous team's task.
	var motherID, team, email, child string
	if err := tx.QueryRow(ctx, `SELECT e.mother_id,m.team_id,lower(r.email),e.child_user_id FROM refund_execution e
 JOIN refunds r ON r.id=e.refund_id JOIN refund_mothers m ON m.id=e.mother_id WHERE e.refund_id=$1`, j.ID).Scan(&motherID, &team, &email, &child); err != nil {
		return err
	}
	state := "cleaning"
	if clean {
		state = "watching"
	}
	if success {
		state = "done"
	}
	if !clean && step == "submitting" {
		state = "review"
	}
	if !success && step != "assigned" {
		if _, err := tx.Exec(ctx, `INSERT INTO refund_resource_tasks(id,refund_id,mother_id,team_id,email,user_id,state)
 VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(refund_id,team_id,email) DO NOTHING`, ID(), j.ID, motherID, team, email, child, state); err != nil {
			return err
		}
	}
	_, err := tx.Exec(ctx, `UPDATE refund_resource_tasks SET state=CASE WHEN email=lower($3) THEN $2 ELSE 'cleaning' END,
 user_id=CASE WHEN email=lower($3) THEN COALESCE(NULLIF($4,''),NULLIF((SELECT child_user_id FROM refund_execution WHERE refund_id=$1),''),user_id) ELSE user_id END,
 next_at=now()+interval '5 seconds',watch_until=now()+COALESCE((SELECT (resource_recovery->>'watch_seconds')::int FROM refund_settings WHERE id),60)*interval '1 second',
	 clean_checks=0,lease_token='',lease_until=NULL,updated_at=now() WHERE refund_id=$1 AND team_id=$5 AND state<>'done'`, j.ID, state, email, child, team)
	if err == nil && j.LeaseToken != "" {
		_, err = tx.Exec(ctx, "DELETE FROM refund_member_leases WHERE token=$1", j.LeaseToken)
	}
	return err
}

func resourceCheckpoint(ctx context.Context, tx pgx.Tx, j *RefundJob, c Checkpoint, step string) (bool, error) {
	switch c.Method {
	case "mother_check_started", "mother_check_full", "mother_check_completed":
		if !oneOf(step, "assigned", "inviting", "invited", "login", "ready") {
			return true, ErrState
		}
		messages := map[string]string{"mother_check_started": "正在检查母号席位、成员和邀请。", "mother_check_full": "母号席位不足，正在检查残留占位。", "mother_check_completed": "母号检查通过。"}
		_, err := tx.Exec(ctx, "INSERT INTO refund_events(refund_id,message,internal) VALUES($1,$2,true)", j.ID, messages[c.Method])
		// A read-only capacity success does not erase invitation/cleanup failures.
		return true, err
	case "resource_remove":
		email := strings.ToLower(strings.TrimSpace(c.TargetEmail))
		if !oneOf(step, "assigned", "inviting", "invited", "login", "ready") || !oneOf(c.TargetKind, "users", "invites") || email == "" || len(email) > 254 || email == strings.ToLower(j.Account.Email) || email == strings.ToLower(j.Mother.Email) || c.TargetID == "" || len(c.TargetID) > 150 {
			return true, ErrState
		}
		var busy bool
		err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM refunds r JOIN refund_execution e ON e.refund_id=r.id
 WHERE (lower(r.email)=$1 OR e.child_user_id=$3) AND r.id<>$2 AND NOT e.released AND e.mother_released_at IS NULL)
 OR EXISTS(SELECT 1 FROM refund_member_leases WHERE email=$1 AND expires_at>now() AND token<>$4)`, email, j.ID, c.TargetID, j.LeaseToken).Scan(&busy)
		if err != nil {
			return true, err
		}
		if busy {
			return true, ErrState
		}
		if err = armResource(ctx, tx, j, email, c.TargetID, "cleaning"); err != nil {
			return true, err
		}
		_, err = tx.Exec(ctx, `INSERT INTO refund_member_leases(email,token,expires_at) VALUES($1,$2,now()+interval '90 seconds')
 ON CONFLICT(email) DO UPDATE SET token=excluded.token,expires_at=excluded.expires_at WHERE refund_member_leases.expires_at<=now() OR refund_member_leases.token=excluded.token`, email, j.LeaseToken)
		return true, err
	}
	return false, nil
}

func recordResourceFailure(ctx context.Context, tx pgx.Tx, j *RefundJob, step string, d RefundDiagnostic) error {
	var cfg ResourceRecoverySettings
	if err := tx.QueryRow(ctx, "SELECT resource_recovery FROM refund_settings WHERE id").Scan(&cfg); err != nil {
		return err
	}
	if d.Code == "mother_capacity" || motherFailure(d) {
		_, err := tx.Exec(ctx, `INSERT INTO refund_resource_health(scope,failures,blocked,next_at,reason,attempt_key)
 SELECT m.team_id,1,$2 OR $5=1,now()+$3*interval '1 second',$4,$6 FROM refund_mothers m JOIN refund_proxies p ON p.id=m.proxy_id
 WHERE m.id=$1 AND (m.id||':'||md5(m.secret)||':'||md5(p.secret))=$6
 ON CONFLICT(scope) DO UPDATE SET failures=CASE WHEN refund_resource_health.attempt_key=$6 THEN refund_resource_health.failures+1 ELSE 1 END,
 blocked=$2 OR $5=1 OR (refund_resource_health.attempt_key=$6 AND (refund_resource_health.blocked OR refund_resource_health.failures+1 >= $5)),
 next_at=now()+$3*interval '1 second',reason=$4,attempt_key=$6,updated_at=now()`, j.Mother.ID, d.Code == "mother_capacity", cfg.CooldownSeconds, d.Code, cfg.FailureLimit, j.MotherAttemptKey)
		if err != nil {
			return err
		}
		if j.RecoveryMode != "cleanup" && oneOf(step, "inviting", "invited", "login", "ready") && d.Code != "mother_invite_auth" {
			_, err = tx.Exec(ctx, `INSERT INTO refund_execution_diagnostics(refund_id,diagnostic)
 SELECT refund_id,$2::jsonb || jsonb_build_object('resource_switch_cause',true,'failed_mother_attempt',$3::text,'failed_mother_id',$4::text)
 FROM refund_execution WHERE refund_id=$1 AND accepted_at IS NULL AND terminal_code='' AND NOT verification_only AND fallback_phase=''`, j.ID, d, j.MotherAttemptKey, j.Mother.ID)
			if err != nil {
				return err
			}
		}
	}
	if d.Evidence != nil && d.Evidence.Side == "child" && oneOf(d.Evidence.Reason, "login_upstream_unavailable", "chatgpt_rate_limited") && d.Evidence.Stage != "export" {
		var count int
		if err := tx.QueryRow(ctx, `SELECT count(DISTINCT email) FROM (SELECT lower(r.email) email FROM refund_execution_diagnostics d JOIN refunds r ON r.id=d.refund_id
 WHERE d.created_at>now()-$3*interval '1 second' AND diagnostic->'evidence'->>'side'='child'
 AND diagnostic->'evidence'->>'stage'=$2 AND diagnostic->'evidence'->>'reason' IN ('login_upstream_unavailable','chatgpt_rate_limited')
 UNION SELECT lower(email) FROM refunds WHERE id=$1) faults`, j.ID, d.Evidence.Stage, cfg.LoginWindowSeconds).Scan(&count); err != nil {
			return err
		}
		var blocked bool
		if err := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM refund_resource_health WHERE scope='login' AND blocked)").Scan(&blocked); err != nil {
			return err
		}
		if count >= cfg.LoginFailureLimit || blocked {
			_, err := tx.Exec(ctx, `INSERT INTO refund_resource_health(scope,failures,blocked,next_at,reason) VALUES('login',1,true,now()+$1*interval '1 second','login_upstream')
 ON CONFLICT(scope) DO UPDATE SET blocked=true,next_at=now()+$1*interval '1 second',probe_id='',reason='login_upstream',updated_at=now()`, cfg.LoginCooldownSeconds)
			return err
		}
	}
	return nil
}

// Only confirmed cleanup can detach a child that may already have joined.
// Delayed resource observation still blocks this email before the next claim.
func finishMotherResourceCleanup(ctx context.Context, tx pgx.Tx, j *RefundJob, step string) (bool, error) {
	var raw []byte
	var key string
	err := tx.QueryRow(ctx, `SELECT d.diagnostic,d.diagnostic->>'failed_mother_attempt' FROM refund_execution_diagnostics d
 JOIN refund_execution e ON e.refund_id=d.refund_id WHERE e.refund_id=$1 AND d.diagnostic->>'resource_switch_cause'='true'
 AND d.diagnostic->>'failed_mother_id'=e.mother_id AND e.accepted_at IS NULL AND e.terminal_code=''
 AND NOT e.verification_only AND e.fallback_phase='' AND e.recovery_mode='cleanup'
 AND d.created_at >= (SELECT max(created_at) FROM refund_events WHERE refund_id=$1 AND internal AND message='正在发送团队邀请。')
 ORDER BY d.id DESC LIMIT 1`, j.ID).Scan(&raw, &key)
	if err == pgx.ErrNoRows {
		return false, nil
	}
	if err != nil {
		return true, err
	}
	var d RefundDiagnostic
	if err = json.Unmarshal(raw, &d); err != nil {
		return true, err
	}
	if _, err = tx.Exec(ctx, "UPDATE refund_execution SET child_user_id='',mother_released_at=now() WHERE refund_id=$1", j.ID); err != nil {
		return true, err
	}
	if err = tx.QueryRow(ctx, "SELECT mother_failover FROM refund_settings WHERE id").Scan(&j.Settings.MotherFailover); err != nil {
		return true, err
	}
	copyJob := *j
	copyJob.MotherAttemptKey = key
	return true, queueMotherFailover(ctx, tx, &copyJob, step, d, true)
}
