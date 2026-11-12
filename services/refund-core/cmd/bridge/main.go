// Private execution module. StarBridge owns authentication, UI and its sole database.
package main

import (
	"context"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"llm-refund/internal/refundprovider"
	"llm-refund/internal/refundworker"
	"llm-refund/internal/store"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
)

type input struct {
	FeeStatus string                   `json:"fee_status"`
	Focus     string                   `json:"focus"`
	SortBy    string                   `json:"sort_by"`
	SortDir   string                   `json:"sort_dir"`
	Sort      string                   `json:"sort"`
	Start     string                   `json:"start"`
	End       string                   `json:"end"`
	Action    string                   `json:"action"`
	Actor     string                   `json:"actor"`
	ID        string                   `json:"id"`
	Key       string                   `json:"key"`
	Fee       int64                    `json:"fee"`
	Accounts  []refundprovider.Account `json:"accounts"`
	Settings  store.RefundSettings     `json:"settings"`
	Proxy     store.RefundProxy        `json:"proxy"`
	Mother    store.RefundMother       `json:"mother"`
	Query     string                   `json:"query"`
	Status    string                   `json:"status"`
	Page      int                      `json:"page"`
	Size      int                      `json:"size"`
	Resume    string                   `json:"resume"`
	Note      string                   `json:"note"`
	Session   string                   `json:"session"`
}

func dispatch(ctx context.Context, s *store.Store, p refundworker.ProtocolRunner, v input) (any, error) {
	switch v.Action {
	case "health":
		return map[string]any{"ok": true, "runtime_ready": s.RefundRuntimeAvailable()}, nil
	case "create":
		ids, replayed, err := s.CreateRefunds(ctx, v.Actor, v.Key, v.Accounts, v.Fee)
		return map[string]any{"ids": ids, "replayed": replayed}, err
	case "detail":
		r, err := s.Refund(ctx, v.Actor, v.ID)
		if err != nil {
			return nil, err
		}
		e, err := s.Events(ctx, v.Actor, v.ID)
		return map[string]any{"order": r, "events": e}, err
	case "list":
		if v.Page < 1 || v.Page > 100000 || v.Size < 1 || v.Size > 500 || len(v.Query) > 254 {
			return nil, store.ErrInvalid
		}
		items, total, err := s.ListRefunds(ctx, v.Actor, v.Query, v.Status, v.Page, v.Size, v.SortBy, v.SortDir)
		return map[string]any{"items": items, "total": total, "page": v.Page, "page_size": v.Size}, err
	case "cancel":
		r, err := s.Refund(ctx, v.Actor, v.ID)
		if err != nil {
			return nil, err
		}
		if r.Status != "queued" && r.Status != "cancelled" {
			return nil, store.ErrState
		}
		err = s.Transition(ctx, v.Actor, v.ID, "cancelled", "申请已取消，服务费已释放")
		return map[string]bool{"ok": err == nil}, err
	}
	var admin bool
	if err := s.Pool.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM users WHERE id=$1 AND role='admin' AND status='active' AND deleted_at IS NULL)", v.Actor).Scan(&admin); err != nil || !admin {
		return nil, store.ErrForbidden
	}
	switch v.Action {
	case "admin_detail":
		var owner string
		if err := s.Pool.QueryRow(ctx, "SELECT user_id FROM refunds WHERE id=$1", v.ID).Scan(&owner); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return nil, store.ErrNotFound
			}
			return nil, err
		}
		r, err := s.Refund(ctx, owner, v.ID)
		if err != nil {
			return nil, err
		}
		events, err := s.Events(ctx, owner, v.ID)
		if err != nil {
			return nil, err
		}
		execution, err := s.RefundExecution(ctx, v.ID)
		if err != nil {
			return nil, err
		}
		result := map[string]any{"order": r, "events": events}
		// Preserve HUIXU admin detail's action projection, distinct from its timeline.
		if execution != nil && !execution.Released && (r.Status == "attention" || execution.RecoveryMode != "") {
			result["recovery"] = map[string]bool{"running": execution.Running, "needs_review": execution.Step == "submitting", "can_release": execution.AcceptedAt == nil && execution.Step != "submitting"}
		}
		return result, nil
	case "settings":
		cfg, err := s.RefundSettings(ctx)
		if err != nil {
			return nil, err
		}
		m, err := s.RefundMothers(ctx)
		if err != nil {
			return nil, err
		}
		proxies, err := s.RefundProxies(ctx)
		return map[string]any{"settings": cfg, "mothers": m, "proxies": proxies, "provider_ready": s.ProviderReady(ctx), "runtime_ready": s.RefundRuntimeAvailable()}, err
	case "save_settings":
		if v.Settings.Enabled && !s.RefundRuntimeAvailable() {
			return nil, errors.New("代退执行环境尚未就绪")
		}
		return nil, s.SaveRefundSettings(ctx, v.Actor, v.Settings)
	case "save_proxy":
		id, err := s.SaveRefundProxy(ctx, v.Actor, v.Proxy)
		return map[string]string{"id": id}, err
	case "save_mother":
		id, err := s.SaveRefundMother(ctx, v.Actor, v.Mother)
		return map[string]string{"id": id}, err
	case "delete_proxy":
		return nil, s.DeleteRefundResource(ctx, v.Actor, "proxy", v.ID)
	case "delete_mother":
		return nil, s.DeleteRefundResource(ctx, v.Actor, "mother", v.ID)
	case "preview":
		return store.PreviewRefundMother(v.Session)
	case "probe_proxy":
		start := time.Now()
		url, err := s.PrepareProxyProbe(ctx, v.Actor, v.Proxy)
		if err != nil {
			return nil, err
		}
		r, err := p.ProbeProxy(ctx, url)
		if err == nil && v.Proxy.ID != "" && v.Proxy.URL == "" && v.Proxy.Name == "" {
			err = s.SaveResourceCheck(ctx, v.Actor, "proxy", v.Proxy.ID, start, r)
		}
		return r, err
	case "probe_mother":
		start := time.Now()
		job, err := s.PrepareMotherProbe(ctx, v.Actor, v.Mother)
		if err != nil {
			return nil, err
		}
		r, err := p.ProbeMother(ctx, job)
		if err == nil && v.Mother.ID != "" && v.Mother.Name == "" && v.Mother.Email == "" && v.Mother.TeamID == "" && v.Mother.ProxyID == "" && v.Mother.AccessToken == "" && v.Mother.Session == "" && v.Mother.RefreshToken == "" {
			err = s.SaveResourceCheck(ctx, v.Actor, "mother", v.Mother.ID, start, r)
		}
		return r, err
	case "monitor":
		if v.Page < 1 || v.Page > 100000 || v.Size < 1 || v.Size > 500 {
			return nil, store.ErrInvalid
		}
		return s.AdminRefundMonitor(ctx, store.RefundMonitorFilter{Query: v.Query, Status: v.Status, Page: v.Page, Size: v.Size, Sort: v.Sort, SortBy: v.SortBy, SortDir: v.SortDir, FeeStatus: v.FeeStatus, Focus: v.Focus, Start: v.Start, End: v.End})
	case "timeline":
		return s.AdminRefundTimeline(ctx, v.ID)
	case "resume":
		return nil, s.ResumeRefund(ctx, v.Actor, v.ID, v.Resume, v.Note)
	}
	return nil, store.ErrInvalid
}
func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() { io.Copy(io.Discard, os.Stdin); stop() }()
	key, err := base64.StdEncoding.DecodeString(os.Getenv("MASTER_KEY"))
	if err != nil {
		log.Fatal("Invalid encryption configuration")
	}
	s, err := store.Open(ctx, os.Getenv("DATABASE_URL"), key)
	if err != nil {
		log.Fatal("Refund database unavailable")
	}
	defer s.Pool.Close()
	token := os.Getenv("REFUND_INTERNAL_TOKEN")
	if len(token) < 32 {
		log.Fatal("Missing internal authentication")
	}
	p := refundworker.DefaultRunner()
	worker := refundworker.Worker{DB: s, Runner: p}
	done := make(chan struct{})
	go func() { defer close(done); worker.Run(ctx) }()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		log.Fatal("Private listener unavailable")
	}
	// The supervisor reads the port from the pipe; it is never published externally.
	json.NewEncoder(os.Stdout).Encode(map[string]any{"port": listener.Addr().(*net.TCPAddr).Port})
	server := &http.Server{ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 75 * time.Second, Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		if r.Method != "POST" || r.URL.Path != "/rpc" || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+token)) != 1 {
			w.WriteHeader(http.StatusForbidden)
			json.NewEncoder(w).Encode(map[string]string{"error": "Forbidden"})
			return
		}
		var v input
		decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1048576))
		decoder.DisallowUnknownFields()
		if decoder.Decode(&v) != nil {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "请求参数格式不正确"})
			return
		}
		call, cancel := context.WithTimeout(r.Context(), 65*time.Second)
		defer cancel()
		result, e := dispatch(call, s, p, v)
		if e != nil {
			status := 400
			message := e.Error()
			switch {
			case errors.Is(e, store.ErrForbidden):
				status = 403
			case errors.Is(e, store.ErrNotFound):
				status = 404
			case errors.Is(e, store.ErrConflict) || errors.Is(e, store.ErrDuplicate) || errors.Is(e, store.ErrState):
				status = 409
			case errors.Is(e, store.ErrInvalid) || errors.Is(e, store.ErrBalance) || errors.Is(e, store.ErrSubmissionPaused):
			default:
				status = 503
				message = "代退操作暂未完成，请稍后重试"
			}
			w.WriteHeader(status)
			json.NewEncoder(w).Encode(map[string]string{"error": message})
			return
		}
		if result == nil {
			result = map[string]bool{"ok": true}
		}
		json.NewEncoder(w).Encode(result)
	})}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		server.Shutdown(shutdown)
	}()
	if e := server.Serve(listener); e != nil && !errors.Is(e, http.ErrServerClosed) {
		stop()
	}
	<-done
}
