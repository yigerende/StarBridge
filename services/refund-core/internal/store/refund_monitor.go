package store

import (
	"context"
	"encoding/json"
	"strings"
	"time"
)

type RefundMonitorFilter struct {
	Query, Status, FeeStatus, Start, End, Focus, Sort string
	Page, Size                                        int
}

type RefundMonitorRow struct {
	RecoveryAlert   bool       `json:"recovery_alert"`
	RecoveryPending bool       `json:"recovery_pending"`
	NextCheckAt     *time.Time `json:"next_check_at"`
	AdminRefund
	StartedAt     *time.Time `json:"started_at"`
	FinishedAt    *time.Time `json:"finished_at"`
	WaitSeconds   *int64     `json:"wait_seconds"`
	HandleSeconds *int64     `json:"handle_seconds"`
	LongWait      bool       `json:"long_wait"`
	LongRunning   bool       `json:"long_running"`
}

type RefundMonitorSummary struct {
	RecoveryAlert   int   `json:"recovery_alert"`
	RecoveryPending int   `json:"recovery_pending"`
	Total           int   `json:"total"`
	Queued          int   `json:"queued"`
	Running         int   `json:"running"`
	Attention       int   `json:"attention"`
	Succeeded       int   `json:"succeeded"`
	Failed          int   `json:"failed"`
	Cancelled       int   `json:"cancelled"`
	LongWait        int   `json:"long_wait"`
	LongRunning     int   `json:"long_running"`
	Frozen          int64 `json:"frozen"`
	Settled         int64 `json:"settled"`
	Released        int64 `json:"released"`
}

type RefundMonitor struct {
	Items       []RefundMonitorRow   `json:"items"`
	Summary     RefundMonitorSummary `json:"summary"`
	Total       int                  `json:"total"`
	Page        int                  `json:"page"`
	Size        int                  `json:"size"`
	GeneratedAt time.Time            `json:"generated_at"`
}

// All fields are business-level observations. Never select protocol state,
// upstream errors, resource bindings or credentials into this response.
func (s *Store) AdminRefundMonitor(ctx context.Context, f RefundMonitorFilter) (RefundMonitor, error) {
	out := RefundMonitor{Items: []RefundMonitorRow{}, Page: f.Page, Size: f.Size}
	f.Query = strings.TrimSpace(f.Query)
	if len(f.Query) > 254 {
		return out, invalid("搜索内容过长")
	}
	if !oneOf(f.Status, "", "queued", "running", "processing", "verifying", "attention", "succeeded", "failed", "banned", "cancelled", "abnormal", "unsuccessful") || !oneOf(f.FeeStatus, "", "frozen", "settled", "released") || !oneOf(f.Focus, "", "long_wait", "long_running", "recovery_alert") || !oneOf(f.Sort, "", "priority", "newest", "oldest", "updated") {
		return out, invalid("监控筛选条件无效")
	}
	var from, until *time.Time
	loc := time.FixedZone("Asia/Shanghai", 8*60*60)
	for i, label := range []string{f.Start, f.End} {
		if label == "" {
			continue
		}
		d, err := time.ParseInLocation("2006-01-02", label, loc)
		if err != nil || d.Year() < 1900 || d.Year() > 9998 {
			return out, invalid("请选择有效的申请日期")
		}
		if i == 0 {
			from = &d
		} else {
			d = d.AddDate(0, 0, 1)
			until = &d
		}
	}
	if from != nil && until != nil && !from.Before(*until) {
		return out, invalid("结束日期不能早于开始日期")
	}
	if out.Page < 1 {
		out.Page = 1
	}
	if out.Page > 100000 {
		out.Page = 100000
	}
	if out.Size < 1 || out.Size > 100 {
		out.Size = 100
	}
	// One SQL snapshot keeps the full summary, filtered total and page in sync.
	// Only generic public events establish start time; internal steps stay private.
	const query = `WITH scoped AS (
 SELECT r.id,r.email,r.status,r.fee,r.fee_status,r.message,r.created_at,r.updated_at,
 u.id AS user_id,u.name AS user_name,u.email AS user_email,p.started_at,
 COALESCE((NOT e.released OR (r.status='queued' AND e.recovery_mode='mother_switch')) AND e.recovery_started_at<=now()-((cfg.recovery->>'alert_seconds')::int * interval '1 second'),false) AS recovery_alert,
 COALESCE((NOT e.released AND e.recovery_mode<>'' AND ((e.next_attempt_at IS NOT NULL AND (((cfg.recovery->>'enabled')::boolean OR (e.recovery_mode='cleanup' AND e.terminal_code='password_verification_failed')) AND ((e.recovery_mode<>'fallback' AND e.fallback_phase<>'reinvite') OR COALESCE((cfg.recovery->'plan_fallback'->>'enabled')::boolean,false)))) OR e.lease_until>now())) OR
 (r.status='queued' AND e.recovery_mode='mother_switch' AND cfg.enabled AND (cfg.mother_failover->>'enabled')::boolean),false) AS recovery_pending,
 CASE WHEN (NOT e.released AND (((cfg.recovery->>'enabled')::boolean OR (e.recovery_mode='cleanup' AND e.terminal_code='password_verification_failed')) AND ((e.recovery_mode<>'fallback' AND e.fallback_phase<>'reinvite') OR COALESCE((cfg.recovery->'plan_fallback'->>'enabled')::boolean,false)))) OR (r.status='queued' AND e.recovery_mode='mother_switch' AND cfg.enabled AND (cfg.mother_failover->>'enabled')::boolean) THEN e.next_attempt_at END AS next_check_at,
 CASE WHEN r.status IN ('succeeded','failed','banned','cancelled') THEN r.updated_at END AS finished_at
 FROM refunds r JOIN users u ON u.id=r.user_id LEFT JOIN refund_execution e ON e.refund_id=r.id CROSS JOIN refund_settings cfg
 LEFT JOIN LATERAL (SELECT min(created_at) AS started_at FROM refund_events
 WHERE refund_id=r.id AND NOT internal AND message IN ('开始处理','正在继续处理')) p ON true
 WHERE ($1='' OR position(lower($1) in lower(r.email||' '||r.id||' '||u.name||' '||u.email))>0)
 AND ($2::timestamptz IS NULL OR r.created_at >= $2) AND ($3::timestamptz IS NULL OR r.created_at < $3)
), measured AS (
 SELECT *, CASE WHEN started_at IS NOT NULL THEN greatest(0,extract(epoch FROM started_at-created_at))::bigint
 WHEN status='queued' THEN greatest(0,extract(epoch FROM now()-created_at))::bigint
 WHEN status='cancelled' THEN greatest(0,extract(epoch FROM finished_at-created_at))::bigint END AS wait_seconds,
 CASE WHEN started_at IS NOT NULL THEN greatest(0,extract(epoch FROM COALESCE(finished_at,now())-started_at))::bigint END AS handle_seconds,
 (status='queued' AND created_at<=now()-interval '10 minutes') AS long_wait,
 COALESCE(status IN ('processing','verifying') AND started_at<=now()-interval '15 minutes',false) AS long_running
 FROM scoped
), filtered AS (
 SELECT * FROM measured WHERE ($4='' OR status=$4 OR ($4='running' AND status IN ('processing','verifying')) OR ($4='unsuccessful' AND status IN ('failed','banned')) OR ($4='abnormal' AND status IN ('attention','failed','banned')))
 AND ($5='' OR fee_status=$5) AND ($6='' OR ($6='long_wait' AND long_wait) OR ($6='long_running' AND long_running) OR ($6='recovery_alert' AND recovery_alert))
), ordered AS (
 SELECT *,row_number() OVER (ORDER BY
 CASE WHEN $7 IN ('','priority') THEN CASE WHEN recovery_alert THEN -1 WHEN status='attention' THEN 0 WHEN long_running THEN 1 WHEN status IN ('processing','verifying') THEN 2 WHEN status='queued' THEN 3 ELSE 4 END END,
 CASE WHEN $7='oldest' OR ($7 IN ('','priority') AND status IN ('queued','processing','verifying','attention')) THEN created_at END ASC,
 CASE WHEN $7='updated' THEN updated_at END DESC,created_at DESC,id) AS rn FROM filtered
), summary AS (
 SELECT count(*) FILTER(WHERE recovery_alert) AS recovery_alert,count(*) FILTER(WHERE recovery_pending) AS recovery_pending,count(*) AS total,count(*) FILTER(WHERE status='queued') AS queued,
 count(*) FILTER(WHERE status IN ('processing','verifying')) AS running,count(*) FILTER(WHERE status='attention') AS attention,
 count(*) FILTER(WHERE status='succeeded') AS succeeded,count(*) FILTER(WHERE status IN ('failed','banned')) AS failed,
 count(*) FILTER(WHERE status='cancelled') AS cancelled,count(*) FILTER(WHERE long_wait) AS long_wait,count(*) FILTER(WHERE long_running) AS long_running,
 COALESCE(sum(fee) FILTER(WHERE fee_status='frozen'),0) AS frozen,COALESCE(sum(fee) FILTER(WHERE fee_status='settled'),0) AS settled,
 COALESCE(sum(fee) FILTER(WHERE fee_status='released'),0) AS released FROM measured
)
SELECT row_to_json(summary), (SELECT count(*) FROM filtered),
 COALESCE((SELECT jsonb_agg(to_jsonb(p)-'rn' ORDER BY rn) FROM (SELECT * FROM ordered ORDER BY rn LIMIT $8 OFFSET $9) p),'[]'::jsonb),now() FROM summary`
	var summary, rows []byte
	err := s.Pool.QueryRow(ctx, query, f.Query, from, until, f.Status, f.FeeStatus, f.Focus, f.Sort, out.Size, (out.Page-1)*out.Size).Scan(&summary, &out.Total, &rows, &out.GeneratedAt)
	if err != nil {
		return out, err
	}
	if err = json.Unmarshal(summary, &out.Summary); err != nil {
		return out, err
	}
	if err = json.Unmarshal(rows, &out.Items); err != nil {
		return out, err
	}
	for i := range out.Items {
		sanitizeRefund(&out.Items[i].Refund)
	}
	return out, nil
}

func oneOf(value string, choices ...string) bool {
	for _, choice := range choices {
		if value == choice {
			return true
		}
	}
	return false
}
