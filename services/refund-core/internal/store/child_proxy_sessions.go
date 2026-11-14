package store

import (
	"context"
	"crypto/rand"
	"fmt"
	"math/big"
	"net/url"
	"regexp"
	"strings"

	"github.com/jackc/pgx/v5"
)

var childCountry = regexp.MustCompile(`^[A-Z]{2}$`)
var royalParams = regexp.MustCompile(`(?i)_(?:country|session|lifetime)-[^_]*`)
var cliParams = regexp.MustCompile(`(?i)-(?:region|sid|t)-[^-]*`)
var sevenParams = regexp.MustCompile(`(?i)-(?:region|session|sessTime|sessAuto)-[^-]*`)
var sevenAuto = regexp.MustCompile(`(?i)-sessAuto-([^-]*)`)

func (v *RefundProxy) sessionDefaults() {
	if v.Vendor == "" {
		v.Vendor = "other"
	}
	if v.Country == "" {
		v.Country = "PH"
	}
	v.Country = strings.ToUpper(strings.TrimSpace(v.Country))
	if v.SessionMinutes == 0 {
		v.SessionMinutes = 60
	}
}

// Same supplier parameter formats as subscription manual_proxy_sessions.py.
// Country and lifetime are resource settings; credentials never leave the server.
func childSessionEndpoint(v RefundProxy, endpoint, ref string) (string, error) {
	v.sessionDefaults()
	max, min := 10080, 1
	switch v.Vendor {
	case "other", "iproyal":
	case "cliproxy", "1024proxy":
		max = 120
	case "711proxy":
		min, max = 5, 180
	default:
		return "", invalid("请选择有效的代理厂商")
	}
	if !childCountry.MatchString(v.Country) || v.SessionMinutes < min || v.SessionMinutes > max {
		return "", invalid(fmt.Sprintf("国家须为两位国家代码，会话时长须为 %d～%d 分钟", min, max))
	}
	u, err := url.Parse(endpoint)
	if err != nil || u.Hostname() == "" {
		return "", invalid("代理地址格式错误")
	}
	if v.Vendor == "other" {
		return endpoint, nil
	}
	if u.User == nil {
		return "", invalid("所选厂商的会话代理需要用户名和密码")
	}
	username := u.User.Username()
	password, _ := u.User.Password()
	if username == "" || password == "" {
		return "", invalid("所选厂商的会话代理需要用户名和密码")
	}
	switch v.Vendor {
	case "iproyal":
		duration := fmt.Sprintf("%dm", v.SessionMinutes)
		if v.SessionMinutes%60 == 0 {
			duration = fmt.Sprintf("%dh", v.SessionMinutes/60)
		}
		password = royalParams.ReplaceAllString(password, "") + "_country-" + strings.ToLower(v.Country) + "_session-" + ref + "_lifetime-" + duration
	case "cliproxy", "1024proxy":
		username = cliParams.ReplaceAllString(username, "") + fmt.Sprintf("-region-%s-sid-%s-t-%d", v.Country, ref, v.SessionMinutes)
	case "711proxy":
		auto := sevenAuto.FindStringSubmatch(username)
		username = sevenParams.ReplaceAllString(username, "") + fmt.Sprintf("-region-%s-session-%s-sessTime-%d", v.Country, ref, v.SessionMinutes)
		if len(auto) > 1 {
			username += "-sessAuto-" + auto[1]
		}
	}
	u.User = url.UserPassword(username, password)
	return u.String(), nil
}

func childSessionRef(vendor string) (string, error) {
	if vendor == "other" {
		return "", nil
	}
	if vendor == "711proxy" {
		n, err := rand.Int(rand.Reader, big.NewInt(100000000))
		if err != nil {
			return "", err
		}
		return fmt.Sprintf("%08d", n.Int64()), nil
	}
	return ID()[:8], nil
}

func childSessionLabel(id string, generation int) string {
	return fmt.Sprintf("child-session:%s:%d", id, generation)
}

// The caller holds the scheduler advisory lock and the execution row lock.
// Only a recorded network failure may replace an existing binding, including
// when the same provider becomes usable again. Expiry never silently rotates it.
func (s *Store) bindChildSession(ctx context.Context, tx pgx.Tx, j *RefundJob, proxyID string, replace bool) error {
	var previous string
	var generation int
	var encrypted []byte
	err := tx.QueryRow(ctx, `SELECT proxy_id,generation,endpoint_secret FROM refund_child_proxy_sessions
 WHERE refund_id=$1 ORDER BY generation DESC LIMIT 1`, j.ID).Scan(&previous, &generation, &encrypted)
	if err != nil && err != pgx.ErrNoRows {
		return err
	}
	if err == nil && !replace {
		if previous != proxyID {
			return ErrState
		}
		return s.openSecret(childSessionLabel(j.ID, generation), encrypted, &j.ChildProxy)
	}
	var v RefundProxy
	var secret []byte
	var endpoint string
	if err = tx.QueryRow(ctx, "SELECT secret,vendor,country,session_minutes FROM refund_proxies WHERE id=$1 AND enabled", proxyID).Scan(&secret, &v.Vendor, &v.Country, &v.SessionMinutes); err != nil {
		return err
	}
	if err = s.openSecret("proxy:"+proxyID, secret, &endpoint); err != nil {
		return err
	}
	ref := ""
	for tries := 0; tries < 10; tries++ {
		ref, err = childSessionRef(v.Vendor)
		if err != nil {
			return err
		}
		if ref == "" {
			break
		}
		var used bool
		if err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM refund_child_proxy_sessions WHERE session_ref=$1)", ref).Scan(&used); err != nil {
			return err
		}
		if !used {
			break
		}
		if tries == 9 {
			return fmt.Errorf("child proxy session allocation unavailable")
		}
	}
	endpoint, err = childSessionEndpoint(v, endpoint, ref)
	if err != nil {
		return err
	}
	generation++
	encrypted, err = s.sealSecret(childSessionLabel(j.ID, generation), endpoint)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO refund_child_proxy_sessions(refund_id,generation,proxy_id,endpoint_secret,vendor,country,session_minutes,session_ref,expires_at)
 VALUES($1,$2,$3,$4,$5,$6,$7,$8,now()+$7::integer*interval '1 minute')`, j.ID, generation, proxyID, encrypted, v.Vendor, v.Country, v.SessionMinutes, ref)
	if err == nil {
		j.ChildProxy = endpoint
	}
	return err
}
