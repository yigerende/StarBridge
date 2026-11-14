package store

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/jackc/pgx/v5"
	"strings"
	"time"
)

// Fixed display text only. Never return raw upstream errors or private facts.
func timelineFailure(d RefundDiagnostic) (stage, reason string) {
	d = d.Sanitized()
	e := d.Evidence
	if e == nil {
		return "", ""
	}
	switch e.Stage {
	case "login", "csrf", "signin", "authorize", "identifier", "password", "mfa_challenge", "mfa_verify", "workspace", "callback", "session", "sentinel", "email_verification", "fallback_login":
		stage = "登"
	case "export":
		stage = "停"
	case "fallback_plan":
		stage = "验"
	case "member_remove", "fallback_remove":
		stage = "移"
	default:
		return "", ""
	}
	if stage == "停" && (d.HTTPStatus >= 400 || d.Code == "export_uncertain") {
		if d.HTTPStatus >= 400 {
			return stage, fmt.Sprintf("提交停用返回 HTTP %d，实际结果需核验", d.HTTPStatus)
		}
		return stage, "提交停用后未取得明确响应，实际结果需核验"
	}
	if e.Event != "failure" && e.Event != "error" && e.PlanCheck == nil {
		return "", ""
	}
	switch {
	case e.Reason == "network_error":
		switch e.Exception {
		case "SSLError":
			reason = "网络连接失败（TLS 握手异常）"
		case "TimeoutError", "ConnectTimeout", "ReadTimeout":
			reason = "网络请求超时"
		default:
			reason = "网络或代理连接中断"
		}
	case oneOf(e.Reason, "chatgpt_rate_limited", "rate_limit_exceeded") || d.HTTPStatus == 429:
		reason = "上游请求过于频繁（429），暂时无法继续"
	case e.Reason == "phone_verification_skipped":
		reason = "上游要求手机验证，本次登录未完成"
	case oneOf(e.Reason, "extra_verification", "passkey_or_challenge"):
		reason = "账号需要额外验证，本次登录未完成"
	case oneOf(e.Reason, "incorrect_password", "invalid_password"):
		reason = "账号密码验证未通过"
	case oneOf(e.Reason, "account_deactivated", "account_disabled", "account_banned", "account_deleted", "account_suspended"):
		reason = "上游提示账号已停用或受限"
	case e.Reason == "team_session_not_ready":
		reason = "登录后未取得本次目标团队的会话"
	case e.Reason == "mfa_state_conflict":
		reason = "两步验证会话冲突（409）"
	case e.PlanCheck != nil && e.PlanCheck.Result == "rejected":
		reason = map[string]string{"personal_account_mismatch": "套餐响应中的账号或空间类型与当前个人账号不一致", "live_email_mismatch": "套餐核验的登录身份不一致", "missing_accounts": "套餐响应缺少账号列表", "account_missing": "套餐响应中未找到当前账号", "conflicting_plan": "上游返回的套餐信息不一致", "conflicting_subscription_state": "套餐与有效订阅状态不一致", "unknown_plan": "上游返回了未识别的套餐类型", "query_failed": "套餐查询未成功"}[e.PlanCheck.Reason]
		if reason == "" {
			reason = "套餐核验未取得可确认的结果"
		}
	case d.HTTPStatus >= 400:
		reason = fmt.Sprintf("上游请求返回 HTTP %d，本次未完成", d.HTTPStatus)
	}
	return stage, reason
}

func readTimelineFailures(ctx context.Context, tx pgx.Tx, id string, out *RefundTimeline) error {
	rows, err := tx.Query(ctx, "SELECT created_at,diagnostic FROM refund_execution_diagnostics WHERE refund_id=$1 ORDER BY created_at,id", id)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var at time.Time
		var raw []byte
		var d RefundDiagnostic
		if err = rows.Scan(&at, &raw); err != nil {
			return err
		}
		if json.Unmarshal(raw, &d) != nil {
			continue
		}
		stage, reason := timelineFailure(d)
		if reason == "" {
			continue
		}
		for i := len(out.Items) - 1; i >= 0; i-- {
			item := &out.Items[i]
			if item.Stage != stage || item.StartedAt == nil || at.Before(*item.StartedAt) {
				continue
			}
			// Completion event and its diagnostic are adjacent transactions. A small
			// tolerance includes the final report without mixing later login attempts.
			if item.FinishedAt != nil && at.After(item.FinishedAt.Add(2*time.Second)) {
				break
			}
			if oneOf(item.Status, "已完成", "已受理", "已确认") {
				if stage == "停" && !strings.Contains(item.Reason, reason) {
					item.Reason = reason + "；" + item.Reason
				}
			} else {
				item.Reason = reason
			}
			break
		}
	}
	return rows.Err()
}
