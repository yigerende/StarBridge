package store

import (
	"context"
	"github.com/jackc/pgx/v5"
)

type MotherFailoverSettings struct {
	Enabled      bool `json:"enabled"`
	MaxSwitches  int  `json:"max_switches"`
	RetrySeconds int  `json:"retry_seconds"`
}

func (v MotherFailoverSettings) Validate() error {
	if v.MaxSwitches < 0 || v.MaxSwitches > 10 || v.RetrySeconds < 5 || v.RetrySeconds > 3600 {
		return invalid("换母号次数须为 0～10 次，排队重试间隔须为 5～3600 秒")
	}
	return nil
}

// Only the initial, read-only preflight can release a reservation for switching.
// Inviting and all later stages may have upstream effects and cannot use this path.
func scheduleMotherFailover(ctx context.Context, tx pgx.Tx, j *RefundJob, step string, d RefundDiagnostic) (bool, error) {
	if step != "assigned" || !oneOf(d.Code, "mother_preflight_auth", "mother_preflight_failed") {
		return false, nil
	}
	var untouched bool
	if err := tx.QueryRow(ctx, "SELECT child_user_id='' AND accepted_at IS NULL FROM refund_execution WHERE refund_id=$1", j.ID).Scan(&untouched); err != nil {
		return true, err
	}
	if !untouched {
		return false, nil
	}
	if err := recordDiagnostic(ctx, tx, j.ID, d); err != nil {
		return true, err
	}
	if d.Code == "mother_preflight_auth" && j.MotherFingerprint != "" {
		if _, err := tx.Exec(ctx, "UPDATE refund_mothers SET auth_failed_fingerprint=$2 WHERE id=$1 AND md5(secret)=$2", j.Mother.ID, j.MotherFingerprint); err != nil {
			return true, err
		}
	}
	// A temporary outage must not exclude every configured mother forever.
	// Keep the same switch budget and queue delay; prefer another free mother,
	// but allow the original resource to be rechecked after the cooldown.
	temporary := d.Code == "mother_preflight_failed" && (d.HTTPStatus == 408 || d.HTTPStatus == 429 ||
		d.HTTPStatus == 500 || d.HTTPStatus == 502 || d.HTTPStatus == 503 || d.HTTPStatus == 504 ||
		(d.HTTPStatus == 0 && (d.NetworkCode == 5 || d.NetworkCode == 6 || d.NetworkCode == 7 ||
			d.NetworkCode == 28 || d.NetworkCode == 35 || d.NetworkCode == 52 || d.NetworkCode == 55 || d.NetworkCode == 56)))
	return true, queueMotherFailover(ctx, tx, j, step, d, temporary)
}

// Callers must establish either untouched preflight or confirmed invitation cleanup.
func queueMotherFailover(ctx context.Context, tx pgx.Tx, j *RefundJob, step string, d RefundDiagnostic, temporary bool) error {
	var switches int
	if err := tx.QueryRow(ctx, "SELECT mother_switch_count FROM refund_execution WHERE refund_id=$1", j.ID).Scan(&switches); err != nil {
		return err
	}
	cfg := j.Settings.MotherFailover
	if cfg == nil || !cfg.Enabled || switches >= cfg.MaxSwitches {
		return autoReleaseRefund(ctx, tx, j, step, d)
	}
	delay := cfg.RetrySeconds
	if temporary && d.Evidence != nil && d.Evidence.RetryAfterSeconds > delay {
		delay = d.Evidence.RetryAfterSeconds
	}
	// No login/submission occurred, and any possible invitation is confirmed absent.
	// Do not enable generic processing -> queued transitions elsewhere.
	if _, err := tx.Exec(ctx, `UPDATE refunds SET status='queued',message=$2,updated_at=now() WHERE id=$1 AND fee_status='frozen'`, j.ID, publicRefundMessage("queued", "")); err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, `UPDATE refund_execution SET step='assigned',released=true,lease_token='',lease_until=NULL,recovery_mode='mother_switch',cleanup_count=0,
 next_attempt_at=now()+($2 * interval '1 second'),mother_switch_count=mother_switch_count+1,
 failed_mothers=CASE WHEN $4 THEN failed_mothers ELSE array_append(failed_mothers,$3) END,recovery_started_at=COALESCE(recovery_started_at,now()),updated_at=now()
 WHERE refund_id=$1 AND child_user_id='' AND accepted_at IS NULL`, j.ID, delay, j.MotherAttemptKey, temporary); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, "INSERT INTO refund_events(refund_id,message,internal) VALUES($1,'资源暂不可用，已重新排队等待调度。',true)", j.ID)
	return err
}
