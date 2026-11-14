package store

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
)

var ErrAccountRestricted = errors.New("该账号已被上游停用，再次提交次数已用完；账号恢复后请联系客服")
var ErrAccountFailuresRestricted = errors.New("该账号连续办理失败，已暂停再次提交；请联系客服核实后解除限制")

// Count terminal orders, never login attempts or idempotent request replays.
func recordAccountOrderOutcome(ctx context.Context, tx pgx.Tx, id, status string) error {
	if status == "succeeded" {
		_, err := tx.Exec(ctx, `UPDATE refund_account_restrictions b SET failure_count=0,
 cleared_at=CASE WHEN b.reason='consecutive_failures' THEN now() ELSE b.cleared_at END
 FROM refunds r WHERE r.id=$1 AND b.email=lower(r.email)`, id)
		return err
	}
	if status != "failed" && status != "banned" {
		return nil
	}
	_, err := tx.Exec(ctx, `INSERT INTO refund_account_restrictions(email,reason,first_seen_at,last_seen_at,refund_id,failure_count)
 SELECT lower(email),'consecutive_failures',now(),now(),id,1 FROM refunds WHERE id=$1
 ON CONFLICT(email) DO UPDATE SET reason=CASE WHEN refund_account_restrictions.cleared_at IS NOT NULL THEN 'consecutive_failures' ELSE refund_account_restrictions.reason END,last_seen_at=now(),refund_id=excluded.refund_id,
 failure_count=CASE WHEN refund_account_restrictions.cleared_at IS NOT NULL THEN 1 ELSE refund_account_restrictions.failure_count+1 END,
 first_seen_at=CASE WHEN refund_account_restrictions.cleared_at IS NOT NULL THEN now() ELSE refund_account_restrictions.first_seen_at END,
 cleared_at=NULL,cleared_by=NULL
 WHERE (refund_account_restrictions.refund_id<>excluded.refund_id OR refund_account_restrictions.reason<>'consecutive_failures')
 AND (refund_account_restrictions.cleared_at IS NULL OR (SELECT created_at FROM refunds WHERE id=$1)>refund_account_restrictions.cleared_at)`, id)
	return err
}

// Only explicit child-account termination is evidence. A generic 403, a
// deactivated personal workspace, and network/authentication failures are not.
func recordAccountRestriction(ctx context.Context, tx pgx.Tx, id string, d RefundDiagnostic) error {
	if d.Code != "account_unavailable" || d.HTTPStatus < 400 || d.Evidence == nil || d.Evidence.Side != "child" {
		return nil
	}
	reason := d.Evidence.UpstreamCode
	if reason == "" {
		reason = d.Evidence.Reason
	}
	if !oneOf(reason, "account_deactivated", "account_deleted", "account_banned", "account_disabled", "account_suspended") {
		return nil
	}
	_, err := tx.Exec(ctx, `INSERT INTO refund_account_restrictions(email,reason,first_seen_at,last_seen_at,refund_id)
 SELECT lower(email),$2,now(),now(),id FROM refunds WHERE id=$1
 ON CONFLICT(email) DO UPDATE SET reason=excluded.reason,last_seen_at=excluded.last_seen_at,
 refund_id=excluded.refund_id,
 first_seen_at=CASE WHEN refund_account_restrictions.cleared_at IS NULL THEN refund_account_restrictions.first_seen_at ELSE excluded.first_seen_at END,
 resubmissions=CASE WHEN refund_account_restrictions.cleared_at IS NULL THEN refund_account_restrictions.resubmissions ELSE 0 END,
 cleared_at=NULL,cleared_by=NULL
 WHERE refund_account_restrictions.cleared_at IS NULL
 OR (SELECT created_at FROM refunds WHERE id=$1)>refund_account_restrictions.cleared_at`, id, reason)
	return err
}

// This runs inside the admission transaction, after idempotency replay and
// duplicate checks. Rejected batches roll back both the counter and the wallet.
func reserveAccountResubmission(ctx context.Context, tx pgx.Tx, email string) error {
	var blocked bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM refund_account_restrictions b CROSS JOIN refund_settings s
 WHERE b.email=$1 AND b.cleared_at IS NULL
 AND COALESCE((s.resource_recovery->>'account_failure_limit')::int,4)>0
 AND b.failure_count>=COALESCE((s.resource_recovery->>'account_failure_limit')::int,4))`, email).Scan(&blocked); err != nil {
		return err
	}
	if blocked {
		return ErrAccountFailuresRestricted
	}
	var used, limit int
	err := tx.QueryRow(ctx, `SELECT b.resubmissions,s.deactivated_resubmit_limit
 FROM refund_account_restrictions b CROSS JOIN refund_settings s
 WHERE b.email=$1 AND b.cleared_at IS NULL AND b.reason<>'consecutive_failures' AND s.id FOR UPDATE OF b`, email).Scan(&used, &limit)
	if err == pgx.ErrNoRows {
		return nil
	}
	if err != nil {
		return err
	}
	if used >= limit {
		return ErrAccountRestricted
	}
	_, err = tx.Exec(ctx, "UPDATE refund_account_restrictions SET resubmissions=resubmissions+1 WHERE email=$1", email)
	return err
}
