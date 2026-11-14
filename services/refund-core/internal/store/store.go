package store

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base32"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"llm-refund/internal/refundprovider"
	"net/mail"
	"net/url"
	"strings"
	"sync/atomic"
	"time"
)

type Store struct {
	Pool         *pgxpool.Pool
	cipher       cipher.AEAD
	runtimeReady atomic.Bool
}

var ErrNotFound = errors.New("记录不存在")
var ErrBalance = errors.New("可用积分不足，请联系服务专员充值")
var ErrConflict = errors.New("幂等键已用于不同的申请内容")
var ErrDuplicate = errors.New("账号已有正在处理的申请，请勿重复提交")
var ErrState = errors.New("当前申请状态不支持此操作")
var ErrSubmissionPaused = errors.New("当前账户暂不能提交新申请")

var ErrForbidden = errors.New("没有执行此操作的权限")
var ErrInvalid = errors.New("参数错误")

type Refund struct {
	ErrorCode  string    `json:"error_code,omitempty"`
	HTTPStatus int       `json:"http_status,omitempty"`
	ID         string    `json:"id"`
	Email      string    `json:"email"`
	Status     string    `json:"status"`
	Fee        int64     `json:"fee"`
	FeeStatus  string    `json:"fee_status"`
	Message    string    `json:"message"`
	CreatedAt  time.Time `json:"created_at"`
	UpdatedAt  time.Time `json:"updated_at"`
}
type Event struct {
	ID        int64     `json:"id"`
	Message   string    `json:"message"`
	CreatedAt time.Time `json:"created_at"`
}

const refundColumns = "id,email,status,fee,fee_status,message,created_at,updated_at"

func Open(ctx context.Context, dsn string, master []byte) (*Store, error) {
	if len(master) != 32 {
		return nil, errors.New("invalid master key")
	}
	block, err := aes.NewCipher(master)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, err
	}
	cfg.MaxConns = 4
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}
	if err = pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	return &Store{Pool: pool, cipher: aead}, nil
}
func invalid(message string) error { return fmt.Errorf("%w：%s", ErrInvalid, message) }
func adminLock(ctx context.Context, tx pgx.Tx, actor string) error {
	if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(718374023)"); err != nil {
		return err
	}
	var role, status string
	err := tx.QueryRow(ctx, "SELECT role,status FROM users WHERE id=$1 AND deleted_at IS NULL FOR SHARE", actor).Scan(&role, &status)
	if err != nil || role != "admin" || status != "active" {
		return ErrForbidden
	}
	return nil
}
func audit(ctx context.Context, tx pgx.Tx, actor, action, target string, metadata any) error {
	// Callers supply safe settings, changed flags or the operator's reason only.
	detail, err := json.Marshal(metadata)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, "INSERT INTO audit_events(actor_id,action,target,detail,ip) VALUES($1,$2,$3,$4,'internal')", actor, action, target, "代退操作："+string(detail))
	return err
}

type AdminRefund struct {
	Refund
	UserID    string `json:"user_id"`
	UserEmail string `json:"user_email"`
	UserName  string `json:"user_name"`
}

func ID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}

func Token() string { return ID() + ID() }

func Hash(s string) string { h := sha256.Sum256([]byte(s)); return hex.EncodeToString(h[:]) }

func (s *Store) Encrypt(id string, a refundprovider.Account) ([]byte, error) {
	raw, err := json.Marshal(a)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, s.cipher.NonceSize())
	if _, err = rand.Read(nonce); err != nil {
		return nil, err
	}
	return s.cipher.Seal(nonce, nonce, raw, []byte(id)), nil
}

func (s *Store) Credentials(ctx context.Context, uid, id string) (refundprovider.Account, error) {
	var data []byte
	var a refundprovider.Account
	if err := s.Pool.QueryRow(ctx, "SELECT credentials FROM refunds WHERE user_id=$1 AND id=$2", uid, id).Scan(&data); err != nil {
		return a, ErrNotFound
	}
	n := s.cipher.NonceSize()
	if len(data) < n {
		return a, errors.New("invalid encrypted credentials")
	}
	raw, err := s.cipher.Open(nil, data[:n], data[n:], []byte(id))
	if err != nil {
		return a, err
	}
	err = json.Unmarshal(raw, &a)
	return a, err
}

func ValidateAccounts(accounts []refundprovider.Account) error {
	if len(accounts) < 1 || len(accounts) > 500 {
		return errors.New("每批须包含 1～500 个账号")
	}
	seen := map[string]bool{}
	for i := range accounts {
		a := &accounts[i]
		a.Email = strings.ToLower(strings.TrimSpace(a.Email))
		a.TOTPSecret = strings.TrimSpace(a.TOTPSecret)
		addr, err := mail.ParseAddress(a.Email)
		if err != nil || addr.Address != a.Email || len(a.Email) > 254 {
			return fmt.Errorf("第 %d 行邮箱格式不正确", i+1)
		}
		if len(a.Password) == 0 || len(a.Password) > 1024 {
			return fmt.Errorf("第 %d 行密码不能为空且不能超过 1024 字节", i+1)
		}
		secret := a.TOTPSecret
		if strings.HasPrefix(secret, "otpauth://") {
			u, e := url.Parse(secret)
			if e != nil || u.Host != "totp" {
				return fmt.Errorf("第 %d 行 2FA 链接不正确", i+1)
			}
			secret = u.Query().Get("secret")
		}
		secret = strings.ToUpper(strings.ReplaceAll(secret, " ", ""))
		secret = strings.TrimRight(secret, "=")
		decoded, e := base32.StdEncoding.WithPadding(base32.NoPadding).DecodeString(secret)
		if e != nil || len(decoded) < 10 || len(secret) > 256 {
			return fmt.Errorf("第 %d 行请填写有效的 Base32 2FA 密钥或 otpauth 链接，不是六位验证码", i+1)
		}
		a.TOTPSecret = secret
		if seen[a.Email] {
			return fmt.Errorf("第 %d 行账号重复", i+1)
		}
		seen[a.Email] = true
	}
	return nil
}

func scanRefund(row pgx.Row) (Refund, error) {
	var r Refund
	err := row.Scan(&r.ID, &r.Email, &r.Status, &r.Fee, &r.FeeStatus, &r.Message, &r.CreatedAt, &r.UpdatedAt)
	sanitizeRefund(&r)
	return r, err
}

func (s *Store) Refund(ctx context.Context, uid, id string) (Refund, error) {
	r, err := scanRefund(s.Pool.QueryRow(ctx, "SELECT "+refundColumns+" FROM refunds WHERE user_id=$1 AND id=$2", uid, id))
	if errors.Is(err, pgx.ErrNoRows) {
		err = ErrNotFound
	}
	return r, err
}

func (s *Store) Events(ctx context.Context, uid, id string) ([]Event, error) {
	rows, err := s.Pool.Query(ctx, "SELECT e.id,e.message,e.created_at FROM refund_events e JOIN refunds r ON r.id=e.refund_id WHERE r.user_id=$1 AND r.id=$2 AND NOT e.internal ORDER BY e.id", uid, id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Event{}
	for rows.Next() {
		var e Event
		if err = rows.Scan(&e.ID, &e.Message, &e.CreatedAt); err != nil {
			return nil, err
		}
		e.Message = normalizeLoginFailureMessage(e.Message)
		if !publicProgress(e.Message) {
			continue
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

func (s *Store) ListRefunds(ctx context.Context, uid, q, status string, page, size int, sorting ...string) ([]Refund, int, error) {
	field, direction := "created_at", "desc"
	if len(sorting) > 0 {
		field = sorting[0]
	}
	if len(sorting) > 1 {
		direction = sorting[1]
	}
	ordering, orderErr := refundListOrder(field, direction, false)
	if orderErr != nil {
		return nil, 0, orderErr
	}
	if size < 1 || size > 500 || page < 1 || page > 100000 {
		return nil, 0, invalid("分页参数无效")
	}
	where := " FROM refunds WHERE user_id=$1 AND ($2='' OR position(lower($2) in lower(email))>0 OR position(lower($2) in id)>0) AND ($3='' OR status=$3 OR ($3='active' AND status IN ('queued','processing','verifying')))"
	var count int
	if err := s.Pool.QueryRow(ctx, "SELECT count(*)"+where, uid, q, status).Scan(&count); err != nil {
		return nil, 0, err
	}
	rows, err := s.Pool.Query(ctx, "SELECT "+refundColumns+where+" ORDER BY "+ordering+" LIMIT $4 OFFSET $5", uid, q, status, size, (page-1)*size)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()
	out := []Refund{}
	for rows.Next() {
		r, e := scanRefund(rows)
		if e != nil {
			return nil, 0, e
		}
		out = append(out, r)
	}
	return out, count, rows.Err()
}

func (s *Store) CreateRefunds(ctx context.Context, uid, key string, accounts []refundprovider.Account, fee int64) ([]string, bool, error) {
	if err := ValidateAccounts(accounts); err != nil {
		return nil, false, invalid(err.Error())
	}
	if len(key) < 8 || len(key) > 128 {
		return nil, false, errors.New("Idempotency-Key 长度必须为 8～128 个字符")
	}
	raw, _ := json.Marshal(accounts)
	requestHash := Hash(string(raw))
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return nil, false, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(718374024)"); err != nil {
		return nil, false, err
	}
	var balance, frozen int64
	if err = tx.QueryRow(ctx, "SELECT balance,frozen FROM users WHERE id=$1 AND status='active' AND deleted_at IS NULL FOR UPDATE", uid).Scan(&balance, &frozen); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, false, ErrForbidden
		}
		return nil, false, err
	}
	var oldHash string
	var ids []string
	err = tx.QueryRow(ctx, "SELECT request_hash,refund_ids FROM refund_idempotency WHERE user_id=$1 AND key=$2", uid, key).Scan(&oldHash, &ids)
	if err == nil {
		if oldHash != requestHash {
			return nil, false, ErrConflict
		}
		return ids, true, nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return nil, false, err
	}
	if fee < 0 || fee > 10000000 {
		return nil, false, ErrInvalid
	}
	var enabled bool
	if err = tx.QueryRow(ctx, "SELECT accepting FROM refund_settings WHERE id").Scan(&enabled); err != nil {
		return nil, false, err
	}
	if !enabled {
		return nil, false, ErrSubmissionPaused
	}
	total := int64(len(accounts)) * fee
	if balance-frozen < total {
		return nil, false, ErrBalance
	}
	ids = make([]string, 0, len(accounts))
	for _, a := range accounts {
		var exists bool
		if err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM refunds WHERE lower(email)=$1 AND status IN ('queued','processing','verifying','attention','succeeded'))", a.Email).Scan(&exists); err != nil {
			return nil, false, err
		}
		if exists {
			return nil, false, ErrDuplicate
		}
		if err = reserveAccountResubmission(ctx, tx, a.Email); err != nil {
			return nil, false, err
		}
		id := ID()
		encrypted, e := s.Encrypt(id, a)
		if e != nil {
			return nil, false, e
		}
		if _, err = tx.Exec(ctx, "INSERT INTO refunds(id,user_id,email,credentials,fee) VALUES($1,$2,$3,$4,$5)", id, uid, a.Email, encrypted, fee); err != nil {
			return nil, false, err
		}
		if _, err = tx.Exec(ctx, "INSERT INTO refund_events(refund_id,message) VALUES($1,'申请已受理')", id); err != nil {
			return nil, false, err
		}
		if _, err = tx.Exec(ctx, "INSERT INTO ledger(user_id,refund_order_id,kind,amount,balance_after,frozen_after,reference,description,business,available_change,frozen_change) VALUES($1,$2,'freeze',0,$4,$5,'refund:freeze:'||$2,'代退服务费冻结','refund',-$3::bigint,$3::bigint)", uid, id, fee, balance, frozen+int64(len(ids)+1)*fee); err != nil {
			return nil, false, err
		}
		ids = append(ids, id)
	}
	if _, err = tx.Exec(ctx, "UPDATE users SET frozen=frozen+$2 WHERE id=$1", uid, total); err != nil {
		return nil, false, err
	}
	encoded, _ := json.Marshal(ids)
	if _, err = tx.Exec(ctx, "INSERT INTO refund_idempotency(user_id,key,request_hash,refund_ids) VALUES($1,$2,$3,$4)", uid, key, requestHash, encoded); err != nil {
		return nil, false, err
	}

	return ids, false, tx.Commit(ctx)
}

func (s *Store) Transition(ctx context.Context, uid, id, to, message string) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = transitionTx(ctx, tx, uid, id, to, message); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func transitionTx(ctx context.Context, tx pgx.Tx, uid, id, to, message string) error {
	var balance, frozen int64
	if err := tx.QueryRow(ctx, "SELECT balance,frozen FROM users WHERE id=$1 FOR UPDATE", uid).Scan(&balance, &frozen); err != nil {
		return err
	}
	r, err := scanRefund(tx.QueryRow(ctx, "SELECT "+refundColumns+" FROM refunds WHERE user_id=$1 AND id=$2 FOR UPDATE", uid, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	if err != nil {
		return err
	}
	if r.Status == to {
		return nil
	}
	allowed := map[string]map[string]bool{
		"queued": {"cancelled": true, "processing": true}, "processing": {"verifying": true, "succeeded": true, "failed": true, "banned": true, "attention": true},
		"verifying": {"processing": true, "succeeded": true, "failed": true, "attention": true, "banned": true}, "attention": {"verifying": true, "processing": true, "succeeded": true, "failed": true, "banned": true},
	}
	if !allowed[r.Status][to] {
		return ErrState
	}
	feeStatus := r.FeeStatus
	kind := ""
	delta := int64(0)
	if to == "succeeded" {
		if feeStatus != "frozen" {
			return ErrState
		}
		kind = "charge"
		feeStatus = "settled"
		balance -= r.Fee
		delta = -r.Fee
		_, err = tx.Exec(ctx, "UPDATE users SET balance=balance-$2,frozen=frozen-$2 WHERE id=$1", uid, r.Fee)
	} else if to == "cancelled" || to == "failed" || to == "banned" {
		if feeStatus != "frozen" {
			return ErrState
		}
		kind = "unfreeze"
		feeStatus = "released"
		_, err = tx.Exec(ctx, "UPDATE users SET frozen=frozen-$2 WHERE id=$1", uid, r.Fee)
	}
	if err != nil {
		return err
	}
	if kind != "" {
		if _, err = tx.Exec(ctx, "INSERT INTO ledger(user_id,refund_order_id,kind,amount,balance_after,frozen_after,reference,description,business,available_change,frozen_change) VALUES($1,$2,$3,$4,$5,$6,'refund:'||$3||':'||$2,'代退服务费结算／释放','refund',$7,$8)", uid, id, kind, delta, balance, frozen-r.Fee, delta+r.Fee, -r.Fee); err != nil {
			return err
		}
	}
	message = publicRefundMessage(to, message)
	if _, err = tx.Exec(ctx, "UPDATE refunds SET status=$3,fee_status=$4,message=$5,updated_at=now() WHERE user_id=$1 AND id=$2", uid, id, to, feeStatus, message); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO refund_events(refund_id,message) VALUES($1,$2)", id, message); err != nil {
		return err
	}
	if err = recordAccountOrderOutcome(ctx, tx, id, to); err != nil {
		return err
	}

	return nil
}
