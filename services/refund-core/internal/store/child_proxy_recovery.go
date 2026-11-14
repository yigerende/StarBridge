package store

import (
	"context"
	"github.com/jackc/pgx/v5"
	"time"
)

// A probe is independent of orders, mothers and wallet reservations.
type ChildProxyRecovery struct {
	ID, Token, URL       string
	UpdatedAt, FailureAt time.Time
	Required             int
}

func (s *Store) ClaimChildProxyRecovery(ctx context.Context) (*ChildProxyRecovery, error) {
	if maintenanceWaiting() {
		return nil, nil
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(718374023)"); err != nil {
		return nil, err
	}
	var required *int
	var ids []string
	var interval int
	if err = tx.QueryRow(ctx, "SELECT child_proxy_recovery_success_count,child_proxy_recovery_interval_seconds,array_remove(array_prepend(child_proxy_id,child_proxy_backups),NULL) FROM refund_settings WHERE id").Scan(&required, &interval, &ids); err != nil {
		return nil, err
	}
	if required == nil {
		_, err = tx.Exec(ctx, `UPDATE refund_proxies SET failure_at=NULL,failure_until=NULL,failure_code='',probe_until=NULL,recovery_token='',recovery_successes=0
   WHERE id=ANY($1) AND enabled AND deleted_at IS NULL AND target IN ('child','both') AND failure_until<=now()`, ids)
		if err != nil {
			return nil, err
		}
		return nil, tx.Commit(ctx)
	}
	j := &ChildProxyRecovery{Required: *required, Token: Token()}
	var secret []byte
	var v RefundProxy
	err = tx.QueryRow(ctx, `SELECT id,secret,updated_at,failure_at,vendor,country,session_minutes FROM refund_proxies
  WHERE id=ANY($1) AND enabled AND deleted_at IS NULL AND target IN ('child','both')
  AND failure_at IS NOT NULL AND failure_until<=now() AND (probe_until IS NULL OR probe_until<=now())
  AND (recovery_checked_at IS NULL OR recovery_checked_at<now()-$2*interval '1 second')
  ORDER BY recovery_checked_at NULLS FIRST,id LIMIT 1 FOR UPDATE`, ids, interval).Scan(&j.ID, &secret, &j.UpdatedAt, &j.FailureAt, &v.Vendor, &v.Country, &v.SessionMinutes)
	if err == pgx.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if err = s.openSecret("proxy:"+j.ID, secret, &j.URL); err != nil {
		return nil, err
	}
	ref, e := childSessionRef(v.Vendor)
	if e != nil {
		return nil, e
	}
	j.URL, e = childSessionEndpoint(v, j.URL, ref)
	if e != nil {
		return nil, e
	}
	_, err = tx.Exec(ctx, "UPDATE refund_proxies SET recovery_token=$2,probe_until=now()+interval '60 seconds',recovery_checked_at=now() WHERE id=$1", j.ID, j.Token)
	if err != nil {
		return nil, err
	}
	return j, tx.Commit(ctx)
}

func (s *Store) FinishChildProxyRecovery(ctx context.Context, j *ChildProxyRecovery, passed bool) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(718374023)"); err != nil {
		return err
	}
	var successes, seconds int
	// An edit, disable, new network fault, timeout or changed configuration fences
	// late results. Mother-only resources and removed backup choices never recover here.
	err = tx.QueryRow(ctx, `SELECT p.recovery_successes,c.child_proxy_cooldown_seconds FROM refund_proxies p CROSS JOIN refund_settings c
 WHERE p.id=$1 AND p.recovery_token=$2 AND p.updated_at=$3 AND p.failure_at=$4 AND p.probe_until>now()
 AND p.enabled AND p.deleted_at IS NULL AND p.target IN ('child','both') AND c.child_proxy_recovery_success_count=$5
 AND p.id=ANY(array_prepend(c.child_proxy_id,c.child_proxy_backups)) FOR UPDATE OF p`, j.ID, j.Token, j.UpdatedAt, j.FailureAt, j.Required).Scan(&successes, &seconds)
	if err == pgx.ErrNoRows {
		return nil
	}
	if err != nil {
		return err
	}
	if passed {
		successes++
	} else {
		successes = 0
	}
	restored := successes >= j.Required
	_, err = tx.Exec(ctx, `UPDATE refund_proxies SET recovery_successes=$2,recovery_checked_at=now(),recovery_token='',probe_until=NULL,
 failure_at=CASE WHEN $3 THEN NULL ELSE failure_at END,failure_code=CASE WHEN $3 THEN '' ELSE failure_code END,
 failure_until=CASE WHEN $3 THEN NULL WHEN NOT $4 THEN now()+$5*interval '1 second' ELSE failure_until END WHERE id=$1`, j.ID, successes, restored, passed, seconds)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}
