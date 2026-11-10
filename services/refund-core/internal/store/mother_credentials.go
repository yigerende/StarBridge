package store

import (
	"encoding/base64"
	"encoding/json"
	"strings"
	"time"
)

type MotherCredentialPreview struct {
	Email      string     `json:"email"`
	TeamID     string     `json:"team_id"`
	PlanType   string     `json:"plan_type"`
	ExpiresAt  *time.Time `json:"expires_at"`
	HasRefresh bool       `json:"has_refresh"`
}

type motherCredential struct {
	MotherCredentialPreview
	access, refresh string
}

func credentialString(value map[string]any, key string) string {
	v, _ := value[key].(string)
	return strings.TrimSpace(v)
}
func credentialObject(value map[string]any, key string) map[string]any {
	v, _ := value[key].(map[string]any)
	return v
}
func findMotherCredential(value any, names []string, depth int, found map[string]bool) {
	if depth > 6 {
		return
	}
	switch v := value.(type) {
	case map[string]any:
		for _, key := range names {
			if token := credentialString(v, key); token != "" {
				found[token] = true
			}
		}
		for _, child := range v {
			findMotherCredential(child, names, depth+1, found)
		}
	case []any:
		for _, child := range v {
			findMotherCredential(child, names, depth+1, found)
		}
	}
}

// Decode metadata, as Space does. Actual validity is checked by the upstream.
func parseMotherCredential(raw string) (motherCredential, error) {
	v := motherCredential{}
	raw = strings.TrimSpace(raw)
	if len(raw) == 0 || len(raw) > 131072 {
		return v, invalid("请粘贴有效的 Session JSON 或 Access Token")
	}
	token := raw
	var session map[string]any
	if strings.HasPrefix(raw, "{") || strings.HasPrefix(raw, "[") {
		var value any
		if json.Unmarshal([]byte(raw), &value) != nil {
			return v, invalid("Session JSON 格式无效")
		}
		ats, rts := map[string]bool{}, map[string]bool{}
		findMotherCredential(value, []string{"accessToken", "access_token"}, 0, ats)
		findMotherCredential(value, []string{"refreshToken", "refresh_token"}, 0, rts)
		if len(ats) != 1 || len(rts) > 1 {
			return v, invalid("每次请导入一个母号，且必须包含唯一的 Access Token")
		}
		for at := range ats {
			token = at
		}
		for rt := range rts {
			v.refresh = rt
		}
		session, _ = value.(map[string]any)
	}
	parts := strings.Split(token, ".")
	if len(parts) != 3 || len(token) > 32768 {
		return v, invalid("Access Token 格式无效，无法读取账号信息")
	}
	data, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(parts[1], "="))
	var claims map[string]any
	if err != nil || json.Unmarshal(data, &claims) != nil || claims == nil {
		return v, invalid("Access Token 格式无效，无法读取账号信息")
	}
	auth, profile := credentialObject(claims, "https://api.openai.com/auth"), credentialObject(claims, "https://api.openai.com/profile")
	v.Email = credentialString(profile, "email")
	if v.Email == "" {
		v.Email = credentialString(claims, "email")
	}
	v.TeamID = credentialString(auth, "chatgpt_account_id")
	v.PlanType = credentialString(auth, "chatgpt_plan_type")
	if session != nil {
		user, account := credentialObject(session, "user"), credentialObject(session, "account")
		if email := credentialString(user, "email"); email != "" {
			if v.Email != "" && !strings.EqualFold(email, v.Email) {
				return motherCredential{}, invalid("Session 与 AT 的账号不一致")
			}
			v.Email = email
		}
		if team := credentialString(account, "id"); team != "" {
			if v.TeamID != "" && team != v.TeamID {
				return motherCredential{}, invalid("Session 与 AT 的团队不一致，请使用目标团队凭据")
			}
			v.TeamID = team
		}
		if plan := credentialString(account, "planType"); plan != "" {
			v.PlanType = plan
		}
	}
	if exp, ok := claims["exp"].(float64); ok && exp > 0 && exp < 253402300800 {
		expires := time.Unix(int64(exp), 0).UTC()
		v.ExpiresAt = &expires
	}
	v.Email = strings.ToLower(v.Email)
	v.HasRefresh = v.refresh != ""
	v.access = token
	return v, nil
}

func PreviewRefundMother(raw string) (MotherCredentialPreview, error) {
	v, err := parseMotherCredential(raw)
	return v.MotherCredentialPreview, err
}

func applyMotherCredential(v *RefundMother) error {
	raw := strings.TrimSpace(v.Session)
	if raw == "" {
		raw = strings.TrimSpace(v.AccessToken)
	}
	// Keep existing opaque AT inputs usable when their identity is explicit.
	if raw != "" && (v.Session != "" || strings.Contains(raw, ".") || strings.HasPrefix(raw, "{") || strings.HasPrefix(raw, "[")) {
		parsed, err := parseMotherCredential(raw)
		if err != nil {
			return err
		}
		if v.Email != "" && parsed.Email != "" && !strings.EqualFold(strings.TrimSpace(v.Email), parsed.Email) {
			return invalid("母号邮箱与凭据不一致")
		}
		if v.TeamID != "" && parsed.TeamID != "" && strings.TrimSpace(v.TeamID) != parsed.TeamID {
			return invalid("团队 ID 与凭据不一致")
		}
		if parsed.Email != "" {
			v.Email = parsed.Email
		}
		if parsed.TeamID != "" {
			v.TeamID = parsed.TeamID
		}
		v.AccessToken, v.PlanType, v.TokenExpiresAt = parsed.access, parsed.PlanType, parsed.ExpiresAt
		if v.RefreshToken == "" {
			v.RefreshToken = parsed.refresh
		}
	}
	v.Session = ""
	v.RefreshToken = strings.TrimSpace(v.RefreshToken)
	if len(v.RefreshToken) > 32768 || strings.ContainsAny(v.RefreshToken, "\r\n") {
		return invalid("Refresh Token 格式无效")
	}
	return nil
}
