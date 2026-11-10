package store

import (
	"net"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"unicode"
)

var proxyScheme = regexp.MustCompile(`^[a-zA-Z][a-zA-Z0-9+.-]*://`)

// Normalize a pasted proxy before encrypting it so all workers receive a URL.
func normalizeProxyURL(raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	bad := func() (string, error) {
		return "", invalid("代理地址须为 HTTP、HTTPS、SOCKS5、SOCKS5H URL，或 IP:端口:用户名:密码（默认 HTTP）")
	}
	if raw == "" || len(raw) > 8192 || strings.IndexFunc(raw, unicode.IsControl) >= 0 {
		return bad()
	}
	if !proxyScheme.MatchString(raw) {
		// The fourth field is the entire password, including any further colons.
		var host, rest string
		if strings.HasPrefix(raw, "[") {
			end := strings.Index(raw, "]:")
			if end < 0 {
				return bad()
			}
			host, rest = raw[1:end], raw[end+2:]
			if net.ParseIP(host) == nil {
				return bad()
			}
		} else {
			host, rest, _ = strings.Cut(raw, ":")
		}
		parts := strings.SplitN(rest, ":", 3)
		if len(parts) == 3 {
			if host == "" || parts[0] == "" || parts[1] == "" || parts[2] == "" || strings.ContainsAny(host, "/?#@\\") || strings.IndexFunc(host, unicode.IsSpace) >= 0 {
				return bad()
			}
			raw = (&url.URL{Scheme: "http", Host: net.JoinHostPort(host, parts[0]), User: url.UserPassword(parts[1], parts[2])}).String()
		} else if len(parts) == 1 {
			raw = "http://" + raw
		} else {
			return bad()
		}
	}
	u, err := url.Parse(raw)
	if err != nil || u.Hostname() == "" || strings.IndexFunc(u.Host, unicode.IsSpace) >= 0 || u.Fragment != "" || u.RawQuery != "" || u.ForceQuery || u.Opaque != "" || (u.Path != "" && u.Path != "/") {
		return bad()
	}
	u.Scheme = strings.ToLower(u.Scheme)
	if u.Scheme != "http" && u.Scheme != "https" && u.Scheme != "socks5" && u.Scheme != "socks5h" {
		return bad()
	}
	if port := u.Port(); port != "" {
		n, err := strconv.Atoi(port)
		if err != nil || n < 1 || n > 65535 {
			return bad()
		}
	} else if strings.HasSuffix(u.Host, ":") {
		return bad()
	}
	return u.String(), nil
}
