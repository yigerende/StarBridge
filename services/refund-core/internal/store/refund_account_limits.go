package store

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
)

var ErrAccountRestricted = errors.New("该账号已被上游停用，再次提交次数已用完；账号恢复后请联系客服")

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
	var used, limit int
	err := tx.QueryRow(ctx, `SELECT b.resubmissions,s.deactivated_resubmit_limit
 FROM refund_account_restrictions b CROSS JOIN refund_settings s
 WHERE b.email=$1 AND b.cleared_at IS NULL AND s.id FOR UPDATE OF b`, email).Scan(&used, &limit)
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
