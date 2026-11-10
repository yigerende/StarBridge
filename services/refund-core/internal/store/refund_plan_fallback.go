package store

import (
	"context"
	"github.com/jackc/pgx/v5"
)

type PlanFallbackSettings struct {
	Enabled      bool `json:"enabled"`
	MaxCycles    int  `json:"max_cycles"`
	MaxAttempts  int  `json:"max_attempts"`
	RetrySeconds int  `json:"retry_seconds"`
}

func (v PlanFallbackSettings) Validate() error {
	if v.MaxCycles < 1 || v.MaxCycles > 5 || v.MaxAttempts < 1 || v.MaxAttempts > 10 || v.RetrySeconds < 5 || v.RetrySeconds > 3600 {
		return invalid("套餐兜底最多 1～5 轮，每轮核验 1～10 次，间隔 5～3600 秒")
	}
	return nil
}

// Called with the user, refund and execution rows locked and the lease checked.
// A free-plan result is the owner's business completion rule, not a forged
// export acceptance. The accepted_at fact remains untouched.
func planFallbackCheckpoint(ctx context.Context, tx pgx.Tx, j *RefundJob, c Checkpoint, step string) error {
	if step != "submitting" || j.RecoveryMode != "fallback" {
		return ErrState
	}
	var phase, child string
	var cycles int
	var verificationOnly bool
	if err := tx.QueryRow(ctx, "SELECT fallback_phase,fallback_cycles,child_user_id,verification_only FROM refund_execution WHERE refund_id=$1", j.ID).Scan(&phase, &cycles, &child, &verificationOnly); err != nil {
		return err
	}
	if child == "" {
		return ErrState
	}
	event := ""
	switch c.Method {
	case "fallback_removing":
		if phase != "" && phase != "removing" {
			return ErrState
		}
		if phase == "" {
			cycles++
		}
		phase, event = "removing", "兜底：正在移出本次账号。"
	case "fallback_removed":
		if phase != "removing" {
			return ErrState
		}
		phase, event = "checking", "兜底：已确认移出本次账号。"
		if verificationOnly {
			if _, err := tx.Exec(ctx, "UPDATE refund_execution SET mother_released_at=COALESCE(mother_released_at,now()) WHERE refund_id=$1", j.ID); err != nil {
				return err
			}
		}
	case "fallback_login":
		if phase != "checking" {
			return ErrState
		}
		event = "兜底：正在重新登录个人账号。"
	case "fallback_plan_started":
		if phase != "checking" || c.UserID != child {
			return ErrState
		}
		event = "兜底：个人身份已确认，正在核验套餐。"
	case "fallback_free":
		if phase != "checking" || c.UserID != child {
			return ErrState
		}
		event = "兜底：个人套餐为 Free，按业务规则完成。"
		if _, err := tx.Exec(ctx, "INSERT INTO refund_events(refund_id,message,internal) VALUES($1,$2,true)", j.ID, event); err != nil {
			return err
		}
		if err := transitionTx(ctx, tx, j.UserID, j.ID, "succeeded", "退款服务已完成"); err != nil {
			return err
		}
		_, err := tx.Exec(ctx, `UPDATE refund_execution SET step='completed',completion_basis='plan_free',fallback_phase='free',completed_at=now(),released=true,lease_token='',lease_until=NULL,next_attempt_at=NULL,recovery_mode='',updated_at=now() WHERE refund_id=$1`, j.ID)
		return err
	case "fallback_paid":
		if phase != "checking" || c.UserID != child || verificationOnly {
			return ErrState
		}
		phase, event = "reinvite", "兜底：个人套餐仍为付费，等待重新邀请办理。"
		// Keep the same mother reservation, proxy, fee and request. A fresh worker
		// starts after the persisted delay; it never loops inside this process.
		if _, err := tx.Exec(ctx, "UPDATE refund_execution SET step='inviting',personal_baseline=NULL,retry_count=0,verify_count=0,cleanup_count=0 WHERE refund_id=$1", j.ID); err != nil {
			return err
		}
	case "fallback_paid_unconfirmed":
		if phase != "checking" || c.UserID != child || !verificationOnly {
			return ErrState
		}
		event = "兜底：个人套餐仍为付费，继续核验，不重新提交。"
	default:
		return ErrInvalid
	}
	if _, err := tx.Exec(ctx, "UPDATE refund_execution SET fallback_phase=$2,fallback_cycles=$3,updated_at=now() WHERE refund_id=$1", j.ID, phase, cycles); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, "INSERT INTO refund_events(refund_id,message,internal) VALUES($1,$2,true)", j.ID, event)
	return err
}
