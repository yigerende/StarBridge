package store

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"net/mail"
	"regexp"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

type RefundSettings struct {
	MotherFailover        *MotherFailoverSettings `json:"mother_failover"`
	Recovery              *RefundRecoverySettings `json:"recovery"`
	ChildProxyID          string                  `json:"child_proxy_id"`
	Enabled               bool                    `json:"enabled"`
	MaxParallel           int                     `json:"max_parallel"`
	LoginAttempts         int                     `json:"max_attempts"`
	RetrySeconds          int                     `json:"retry_seconds"`
	MotherIntervalSeconds int                     `json:"mother_interval_seconds"`
}
type RefundProxy struct {
	Check     *ResourceCheck `json:"check,omitempty"`
	ID        string         `json:"id"`
	Name      string         `json:"name"`
	Enabled   bool           `json:"enabled"`
	HasSecret bool           `json:"has_secret"`
	URL       string         `json:"url,omitempty"`
}
type RefundMother struct {
	AuthBlocked    bool           `json:"auth_blocked"`
	Check          *ResourceCheck `json:"check,omitempty"`
	ID             string         `json:"id"`
	Name           string         `json:"name"`
	Email          string         `json:"email"`
	TeamID         string         `json:"team_id"`
	ProxyID        string         `json:"proxy_id"`
	SeatType       string         `json:"seat_type"`
	Enabled        bool           `json:"enabled"`
	HasSecret      bool           `json:"has_secret"`
	AccessToken    string         `json:"access_token,omitempty"`
	Session        string         `json:"session,omitempty"`
	RefreshToken   string         `json:"refresh_token,omitempty"`
	HasRefresh     bool           `json:"has_refresh"`
	PlanType       string         `json:"plan_type"`
	TokenExpiresAt *time.Time     `json:"token_expires_at"`
	ActiveRefund   string         `json:"active_refund"`
	ActiveEmail    string         `json:"active_email"`
	ActiveStatus   string         `json:"active_status"`
}

func (s *Store) sealSecret(id string, value any) ([]byte, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, s.cipher.NonceSize())
	if _, err = rand.Read(nonce); err != nil {
		return nil, err
	}
	return s.cipher.Seal(nonce, nonce, raw, []byte(id)), nil
}
func (s *Store) openSecret(id string, data []byte, value any) error {
	n := s.cipher.NonceSize()
	if len(data) < n {
		return errors.New("invalid encrypted settings")
	}
	raw, err := s.cipher.Open(nil, data[:n], data[n:], []byte(id))
	if err != nil {
		return err
	}
	return json.Unmarshal(raw, value)
}
func (s *Store) RefundSettings(ctx context.Context) (RefundSettings, error) {
	var v RefundSettings
	err := s.Pool.QueryRow(ctx, "SELECT enabled,max_parallel,login_attempts,retry_seconds,COALESCE(child_proxy_id,''),mother_interval_seconds,recovery,mother_failover FROM refund_settings WHERE id").Scan(&v.Enabled, &v.MaxParallel, &v.LoginAttempts, &v.RetrySeconds, &v.ChildProxyID, &v.MotherIntervalSeconds, &v.Recovery, &v.MotherFailover)
	return v, err
}
func (s *Store) ProviderReady(ctx context.Context) bool {
	if !s.RefundRuntimeAvailable() {
		return false
	}
	var ready bool
	_ = s.Pool.QueryRow(ctx, `SELECT enabled AND EXISTS(SELECT 1 FROM refund_proxies p WHERE p.id=child_proxy_id AND p.enabled) AND EXISTS(SELECT 1 FROM refund_mothers m JOIN refund_proxies p ON p.id=m.proxy_id WHERE m.deleted_at IS NULL AND m.enabled AND p.enabled AND (m.token_expires_at IS NULL OR m.token_expires_at>now()) AND (m.auth_failed_fingerprint IS NULL OR m.auth_failed_fingerprint<>md5(m.secret))) FROM refund_settings WHERE id`).Scan(&ready)
	return ready
}
func (s *Store) SetRefundRuntimeAvailable(v bool) { s.runtimeReady.Store(v) }
func (s *Store) RefundRuntimeAvailable() bool     { return s.runtimeReady.Load() }
func (s *Store) SaveRefundSettings(ctx context.Context, actor string, v RefundSettings) error {
	if v.MotherFailover != nil {
		if err := v.MotherFailover.Validate(); err != nil {
			return err
		}
	}
	if v.Recovery != nil {
		if err := v.Recovery.Validate(); err != nil {
			return err
		}
	}
	if v.MotherIntervalSeconds < 0 || v.MotherIntervalSeconds > 86400 {
		return invalid("同母号接单间隔须为 0～86400 秒")
	}
	if v.MaxParallel < 1 || v.MaxParallel > 8 || v.LoginAttempts < 1 || v.LoginAttempts > 3 || v.RetrySeconds < 5 || v.RetrySeconds > 300 {
		return invalid("并行母号 1～8，最多尝试 1～3，重试间隔 5～300 秒")
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = adminLock(ctx, tx, actor); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(718374023)"); err != nil {
		return err
	}
	// Older clients omit this nested setting. Preserve an explicit disabled or
	// customized fallback instead of silently restoring defaults on next startup.
	if v.Recovery != nil && v.Recovery.PlanFallback == nil {
		if err = tx.QueryRow(ctx, "SELECT recovery->'plan_fallback' FROM refund_settings WHERE id FOR UPDATE").Scan(&v.Recovery.PlanFallback); err != nil {
			return err
		}
	}
	if v.Recovery != nil && v.Recovery.VerificationHTTPStatuses == nil {
		if err = tx.QueryRow(ctx, "SELECT COALESCE(recovery->'verification_http_statuses','[404]'::jsonb) FROM refund_settings WHERE id FOR UPDATE").Scan(&v.Recovery.VerificationHTTPStatuses); err != nil {
			return err
		}
	}
	v.ChildProxyID = strings.TrimSpace(v.ChildProxyID)
	if v.ChildProxyID != "" {
		var available bool
		if err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM refund_proxies WHERE id=$1 AND (enabled OR NOT $2))", v.ChildProxyID, v.Enabled).Scan(&available); err != nil {
			return err
		}
		if !available {
			return invalid("请选择已启用的子号全局代理")
		}
	}
	if v.Enabled {
		if v.ChildProxyID == "" {
			return invalid("请先配置子号全局代理")
		}
		var ok bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM refund_mothers m JOIN refund_proxies p ON p.id=m.proxy_id WHERE m.deleted_at IS NULL AND m.enabled AND p.enabled)`).Scan(&ok); err != nil {
			return err
		}
		if !ok {
			return invalid("请先配置并启用母号与代理")
		}
	}
	_, err = tx.Exec(ctx, "UPDATE refund_settings SET enabled=$1,max_parallel=$2,login_attempts=$3,retry_seconds=$4,child_proxy_id=NULLIF($5,''),mother_interval_seconds=$6,recovery=COALESCE($7::jsonb,recovery),mother_failover=COALESCE($8::jsonb,mother_failover),updated_at=now() WHERE id", v.Enabled, v.MaxParallel, v.LoginAttempts, v.RetrySeconds, v.ChildProxyID, v.MotherIntervalSeconds, v.Recovery, v.MotherFailover)
	if err == nil {
		err = audit(ctx, tx, actor, "refund.settings", "settings", v)
	}
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func (s *Store) RefundProxies(ctx context.Context) ([]RefundProxy, error) {
	rows, err := s.Pool.Query(ctx, "SELECT id,name,enabled,check_json FROM refund_proxies ORDER BY name,id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []RefundProxy{}
	for rows.Next() {
		var v RefundProxy
		v.HasSecret = true
		if err = rows.Scan(&v.ID, &v.Name, &v.Enabled, &v.Check); err != nil {
			return nil, err
		}
		out = append(out, v)
	}
	return out, rows.Err()
}
func (s *Store) RefundMothers(ctx context.Context) ([]RefundMother, error) {
	rows, err := s.Pool.Query(ctx, `SELECT m.id,m.name,m.email,m.team_id,m.proxy_id,m.seat_type,m.enabled,COALESCE(e.refund_id,''),COALESCE(r.email,''),COALESCE(r.status,''),m.refresh_secret IS NOT NULL,m.plan_type,m.token_expires_at,m.check_json,COALESCE(m.auth_failed_fingerprint=md5(m.secret),false) FROM refund_mothers m LEFT JOIN refund_execution e ON e.mother_id=m.id AND NOT e.released AND e.mother_released_at IS NULL LEFT JOIN refunds r ON r.id=e.refund_id WHERE m.deleted_at IS NULL ORDER BY m.name,m.id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []RefundMother{}
	for rows.Next() {
		var v RefundMother
		v.HasSecret = true
		if err = rows.Scan(&v.ID, &v.Name, &v.Email, &v.TeamID, &v.ProxyID, &v.SeatType, &v.Enabled, &v.ActiveRefund, &v.ActiveEmail, &v.ActiveStatus, &v.HasRefresh, &v.PlanType, &v.TokenExpiresAt, &v.Check, &v.AuthBlocked); err != nil {
			return nil, err
		}
		out = append(out, v)
	}
	return out, rows.Err()
}
func settingError(err error) error {
	var p *pgconn.PgError
	if errors.As(err, &p) {
		if p.Code == "23505" {
			return invalid("母号邮箱或团队 ID 已存在，不能重复配置同一个母号")
		}
		if p.Code == "23503" {
			return invalid("记录被使用或关联的代理不存在")
		}
	}
	return err
}
func (s *Store) SaveRefundProxy(ctx context.Context, actor string, v RefundProxy) (string, error) {
	v.Name = strings.TrimSpace(v.Name)
	if v.Name == "" || len(v.Name) > 100 {
		return "", invalid("请填写代理名称（最多100字节）")
	}
	v.URL = strings.TrimSpace(v.URL)
	if v.URL != "" {
		var e error
		v.URL, e = normalizeProxyURL(v.URL)
		if e != nil {
			return "", e
		}
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback(ctx)
	if err = adminLock(ctx, tx, actor); err != nil {
		return "", err
	}
	// Serialize settings mutations with scheduling; changing an active exit is forbidden.
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(718374023)"); err != nil {
		return "", err
	}
	fresh := v.ID == ""
	if fresh {
		v.ID = ID()
		if v.URL == "" {
			return "", invalid("请填写代理地址")
		}
	} else {
		var exists, busy bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM refund_proxies WHERE id=$1),EXISTS(SELECT 1 FROM refund_execution e JOIN refund_mothers m ON m.id=e.mother_id WHERE (m.proxy_id=$1 OR e.child_proxy_id=$1) AND e.lease_until>now() AND NOT e.released)`, v.ID).Scan(&exists, &busy); err != nil {
			return "", err
		}
		if !exists {
			return "", ErrNotFound
		}
		if busy && v.URL != "" {
			return "", invalid("代理正在执行任务，待当前任务结束后再修改地址")
		}
	}
	if fresh || v.URL != "" {
		secret, e := s.sealSecret("proxy:"+v.ID, v.URL)
		if e != nil {
			return "", e
		}
		_, err = tx.Exec(ctx, `INSERT INTO refund_proxies(id,name,secret,enabled) VALUES($1,$2,$3,$4) ON CONFLICT(id) DO UPDATE SET name=excluded.name,secret=excluded.secret,enabled=excluded.enabled,check_json=NULL,updated_at=now()`, v.ID, v.Name, secret, v.Enabled)
	} else {
		_, err = tx.Exec(ctx, "UPDATE refund_proxies SET name=$2,enabled=$3,check_json=NULL,updated_at=now() WHERE id=$1", v.ID, v.Name, v.Enabled)
	}
	if err == nil {
		_, err = tx.Exec(ctx, "UPDATE refund_mothers SET check_json=NULL WHERE proxy_id=$1", v.ID)
	}
	if err == nil {
		err = audit(ctx, tx, actor, "refund.proxy.save", v.ID, map[string]any{"name": v.Name, "enabled": v.Enabled, "secret_changed": v.URL != ""})
	}
	if err != nil {
		return "", settingError(err)
	}
	return v.ID, tx.Commit(ctx)
}

var teamIDPattern = regexp.MustCompile(`^[a-zA-Z0-9_-]{8,100}$`)

func (s *Store) SaveRefundMother(ctx context.Context, actor string, v RefundMother) (string, error) {
	if err := applyMotherCredential(&v); err != nil {
		return "", err
	}
	v.Name = strings.TrimSpace(v.Name)
	v.Email = strings.ToLower(strings.TrimSpace(v.Email))
	v.TeamID = strings.TrimSpace(v.TeamID)
	v.AccessToken = strings.TrimSpace(v.AccessToken)
	a, e := mail.ParseAddress(v.Email)
	if v.Name == "" || len(v.Name) > 100 || e != nil || a.Address != v.Email || !teamIDPattern.MatchString(v.TeamID) || (v.SeatType != "default" && v.SeatType != "prolite") {
		return "", invalid("请填写有效的母号名称、邮箱、团队 ID 和席位")
	}
	if len(v.AccessToken) > 32768 || strings.ContainsAny(v.AccessToken, "\r\n") {
		return "", invalid("母号 AT 格式不正确")
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback(ctx)
	if err = adminLock(ctx, tx, actor); err != nil {
		return "", err
	}
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(718374023)"); err != nil {
		return "", err
	}
	fresh := v.ID == ""
	if fresh {
		v.ID = ID()
		if v.AccessToken == "" {
			return "", invalid("请填写母号 AT")
		}
	} else {
		var team, email, proxy, seat string
		var reserved, running bool
		err = tx.QueryRow(ctx, `SELECT team_id,email,proxy_id,seat_type,EXISTS(SELECT 1 FROM refund_execution WHERE mother_id=$1 AND NOT released),EXISTS(SELECT 1 FROM refund_execution WHERE mother_id=$1 AND lease_until>now()) FROM refund_mothers WHERE id=$1 AND deleted_at IS NULL`, v.ID).Scan(&team, &email, &proxy, &seat, &reserved, &running)
		if errors.Is(err, pgx.ErrNoRows) {
			return "", ErrNotFound
		}
		if err != nil {
			return "", err
		}
		if reserved && (team != v.TeamID || email != v.Email || seat != v.SeatType) {
			return "", invalid("母号有未结束任务，不能更换身份、团队或席位")
		}
		if running && (proxy != v.ProxyID || v.AccessToken != "" || v.RefreshToken != "") {
			return "", invalid("任务正在运行，结束后再更新 AT 或代理")
		}
	}
	if fresh || v.AccessToken != "" {
		secret, e := s.sealSecret("mother:"+v.ID, v.AccessToken)
		if e != nil {
			return "", e
		}
		_, err = tx.Exec(ctx, `INSERT INTO refund_mothers(id,name,email,team_id,secret,proxy_id,seat_type,enabled) VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(id) DO UPDATE SET name=excluded.name,email=excluded.email,team_id=excluded.team_id,secret=excluded.secret,proxy_id=excluded.proxy_id,seat_type=excluded.seat_type,enabled=excluded.enabled,check_json=NULL,updated_at=now()`, v.ID, v.Name, v.Email, v.TeamID, secret, v.ProxyID, v.SeatType, v.Enabled)
	} else {
		_, err = tx.Exec(ctx, `UPDATE refund_mothers SET name=$2,email=$3,team_id=$4,proxy_id=$5,seat_type=$6,enabled=$7,check_json=NULL,updated_at=now() WHERE id=$1`, v.ID, v.Name, v.Email, v.TeamID, v.ProxyID, v.SeatType, v.Enabled)
	}
	if err == nil && v.AccessToken != "" {
		_, err = tx.Exec(ctx, "UPDATE refund_mothers SET plan_type=$2,token_expires_at=$3 WHERE id=$1", v.ID, v.PlanType, v.TokenExpiresAt)
	}
	if err == nil && v.RefreshToken != "" {
		var secret []byte
		secret, err = s.sealSecret("mother-refresh:"+v.ID, v.RefreshToken)
		if err == nil {
			_, err = tx.Exec(ctx, "UPDATE refund_mothers SET refresh_secret=$2 WHERE id=$1", v.ID, secret)
		}
	}
	if err == nil {
		err = audit(ctx, tx, actor, "refund.mother.save", v.ID, map[string]any{"name": v.Name, "email": v.Email, "team_id": v.TeamID, "enabled": v.Enabled, "secret_changed": v.AccessToken != "" || v.RefreshToken != ""})
	}
	if err != nil {
		return "", settingError(err)
	}
	return v.ID, tx.Commit(ctx)
}
func (s *Store) DeleteRefundResource(ctx context.Context, actor, kind, id string) error {
	table := "refund_mothers"
	if kind == "proxy" {
		table = "refund_proxies"
	} else if kind != "mother" {
		return ErrInvalid
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = adminLock(ctx, tx, actor); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(718374023)"); err != nil {
		return err
	}
	query := "DELETE FROM " + table + " WHERE id=$1"
	if kind == "mother" {
		// No history/reservation veto. Keep a private tombstone so an existing
		// worker can finish; the scheduler excludes it from all new assignments.
		query = "UPDATE refund_mothers SET deleted_at=COALESCE(deleted_at,now()),updated_at=now() WHERE id=$1"
	}
	tag, err := tx.Exec(ctx, query, id)
	if err != nil {
		return settingError(err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	if err = audit(ctx, tx, actor, "refund."+kind+".delete", id, map[string]any{}); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

type Execution struct {
	RecoveryMode string     `json:"-"`
	RefundID     string     `json:"refund_id"`
	MotherID     string     `json:"mother_id,omitempty"`
	Step         string     `json:"step"`
	ChildUserID  string     `json:"-"`
	AcceptedAt   *time.Time `json:"accepted_at"`
	CompletedAt  *time.Time `json:"completed_at"`
	LeaseToken   string     `json:"-"`
	Running      bool       `json:"running"`
	Released     bool       `json:"released"`
}

func (s *Store) RefundExecution(ctx context.Context, id string) (*Execution, error) {
	var v Execution
	err := s.Pool.QueryRow(ctx, `SELECT refund_id,mother_id,step,child_user_id,accepted_at,completed_at,COALESCE(lease_until>now(),false),released,recovery_mode FROM refund_execution WHERE refund_id=$1`, id).Scan(&v.RefundID, &v.MotherID, &v.Step, &v.ChildUserID, &v.AcceptedAt, &v.CompletedAt, &v.Running, &v.Released, &v.RecoveryMode)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	return &v, err
}
