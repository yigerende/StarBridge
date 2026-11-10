package store

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

type SeatCount struct {
	Total     int `json:"total"`
	Remaining int `json:"remaining"`
	Used      int `json:"used"`
	Held      int `json:"held"`
}
type SeatCapacity struct {
	Standard *SeatCount `json:"standard,omitempty"`
	Premium  *SeatCount `json:"premium,omitempty"`
}

// ResourceCheck contains only server-normalized diagnostics, never protocol data.
type ResourceCheck struct {
	Valid           bool          `json:"valid"`
	HTTPStatus      int           `json:"http_status"`
	LatencyMS       int64         `json:"latency_ms"`
	Code            string        `json:"code"`
	Message         string        `json:"message"`
	CheckedAt       time.Time     `json:"checked_at"`
	IPAddress       string        `json:"ip_address,omitempty"`
	Seats           *SeatCapacity `json:"seats,omitempty"`
	CapacityCode    string        `json:"capacity_code,omitempty"`
	CapacityMessage string        `json:"capacity_message,omitempty"`
}

func (s *Store) PrepareProxyProbe(ctx context.Context, actor string, v RefundProxy) (string, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback(ctx)
	if err = adminLock(ctx, tx, actor); err != nil {
		return "", err
	}
	url := strings.TrimSpace(v.URL)
	if url == "" && v.ID != "" {
		var secret []byte
		err = tx.QueryRow(ctx, "SELECT secret FROM refund_proxies WHERE id=$1", v.ID).Scan(&secret)
		if errors.Is(err, pgx.ErrNoRows) {
			return "", ErrNotFound
		}
		if err != nil {
			return "", err
		}
		if err = s.openSecret("proxy:"+v.ID, secret, &url); err != nil {
			return "", err
		}
	}
	url, err = normalizeProxyURL(url)
	if err != nil {
		return "", err
	}
	return url, tx.Commit(ctx)
}

func (s *Store) SaveResourceCheck(ctx context.Context, actor, kind, id string, started time.Time, result ResourceCheck) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = adminLock(ctx, tx, actor); err != nil {
		return err
	}
	raw, err := json.Marshal(result)
	if err != nil {
		return err
	}
	// Do not replace a newer check or attach old diagnostics to changed settings.
	query := `UPDATE refund_proxies SET check_json=$2 WHERE id=$1 AND updated_at<=$3 AND (check_json IS NULL OR (check_json->>'checked_at')::timestamptz<=$3)`
	if kind == "mother" {
		query = `UPDATE refund_mothers m SET check_json=$2,auth_failed_fingerprint=CASE WHEN ($2::jsonb->>'valid')::boolean THEN NULL WHEN $2::jsonb->>'code'='unauthorized' THEN md5(m.secret) ELSE auth_failed_fingerprint END WHERE id=$1 AND deleted_at IS NULL AND updated_at<=$3 AND EXISTS(SELECT 1 FROM refund_proxies p WHERE p.id=m.proxy_id AND p.updated_at<=$3) AND (check_json IS NULL OR (check_json->>'checked_at')::timestamptz<=$3)`
	} else if kind != "proxy" {
		return ErrInvalid
	}
	if _, err = tx.Exec(ctx, query, id, raw, started); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
