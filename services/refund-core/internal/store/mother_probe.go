package store

import (
	"context"
	"errors"
	"strings"

	"github.com/jackc/pgx/v5"
)

// Only the server-side checker receives decrypted credentials. Never serialize
// this job as an HTTP response.
func (s *Store) PrepareMotherProbe(ctx context.Context, actor string, v RefundMother) (*RefundJob, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	if err = adminLock(ctx, tx, actor); err != nil {
		return nil, err
	}
	if v.ID != "" {
		var saved RefundMother
		var secret []byte
		err = tx.QueryRow(ctx, "SELECT email,team_id,proxy_id,secret FROM refund_mothers WHERE id=$1 AND deleted_at IS NULL", v.ID).Scan(&saved.Email, &saved.TeamID, &saved.ProxyID, &secret)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		if err != nil {
			return nil, err
		}
		if strings.TrimSpace(v.AccessToken) == "" && strings.TrimSpace(v.Session) == "" {
			if err = s.openSecret("mother:"+v.ID, secret, &v.AccessToken); err != nil {
				return nil, err
			}
			if v.Email == "" {
				v.Email = saved.Email
			}
			if v.TeamID == "" {
				v.TeamID = saved.TeamID
			}
		}
		if v.ProxyID == "" {
			v.ProxyID = saved.ProxyID
		}
	}
	if err = applyMotherCredential(&v); err != nil {
		return nil, err
	}
	if v.AccessToken == "" || v.Email == "" || !teamIDPattern.MatchString(v.TeamID) {
		return nil, invalid("请先解析母号凭据并填写团队 ID")
	}
	var proxySecret []byte
	err = tx.QueryRow(ctx, "SELECT secret FROM refund_proxies WHERE id=$1 AND enabled", v.ProxyID).Scan(&proxySecret)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, invalid("请先选择已启用的母号专属代理")
	}
	if err != nil {
		return nil, err
	}
	job := &RefundJob{Mother: v}
	job.Mother.Session, job.Mother.RefreshToken = "", ""
	if err = s.openSecret("proxy:"+v.ProxyID, proxySecret, &job.MotherProxy); err != nil {
		return nil, err
	}
	return job, tx.Commit(ctx)
}
