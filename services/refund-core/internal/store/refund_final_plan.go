package store

import (
	"context"
	"github.com/jackc/pgx/v5"
)

const finalPaidPlanMessage = "已核验个人套餐仍为付费，本次办理未完成，冻结服务费已释放。"
const finalPlanUnknownMessage = "个人套餐核验未完成，处理结果尚未确认。服务费仍冻结、尚未扣除，请联系客服处理原订单，暂勿重复提交。"

// The last check has the same bounded attempts as every earlier check, but no
// re-invitation budget. These helpers run under the original wallet/order locks.
func finishPaidPlan(ctx context.Context, tx pgx.Tx, j *RefundJob) error {
	if err := finishResources(ctx, tx, j, true, false, "submitting"); err != nil {
		return err
	}
	if err := transitionTx(ctx, tx, j.UserID, j.ID, "failed", finalPaidPlanMessage); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, `UPDATE refund_execution SET fallback_phase='paid',released=true,
 mother_released_at=COALESCE(mother_released_at,now()),completed_at=now(),
 lease_token='',lease_until=NULL,recovery_mode='',next_attempt_at=NULL,updated_at=now()
 WHERE refund_id=$1`, j.ID)
	return err
}

func pausePlanVerification(ctx context.Context, tx pgx.Tx, j *RefundJob, phase string) error {
	// Only a persisted removal confirmation can release a physical mother. The
	// unfinished execution continues to fence this target email across customers.
	if phase == "checking" {
		if err := finishResources(ctx, tx, j, true, false, "submitting"); err != nil {
			return err
		}
	}
	if err := transitionTx(ctx, tx, j.UserID, j.ID, "attention", finalPlanUnknownMessage); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, `UPDATE refund_execution SET fallback_final=true,
 mother_released_at=CASE WHEN $2='checking' THEN COALESCE(mother_released_at,now()) ELSE mother_released_at END,
 recovery_started_at=COALESCE(recovery_started_at,now()),lease_token='',lease_until=NULL,
 recovery_mode='',next_attempt_at=NULL,updated_at=now() WHERE refund_id=$1`, j.ID, phase)
	return err
}
