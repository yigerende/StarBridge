//go:build linux || darwin

package main

import (
	"bufio"
	"encoding/json"
	"io"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Private stdin/stdout protocol. Never send these frames to diagnostic/event sinks.
type checkoutCredentialContextKey struct{}
type checkoutCredentialSink func(model_Session) error

func checkoutCredentialInput(in io.Reader, out io.Writer) (model_Session, checkoutCredentialSink, error) {
	scanner := bufio.NewScanner(in)
	scanner.Buffer(make([]byte, 4096), 1024*1024)
	if !scanner.Scan() {
		return model_Session{}, nil, cli_ErrInput
	}
	session, err := cli_parseSessionInput(scanner.Text())
	if err != nil {
		return session, nil, err
	}
	save := func(value model_Session) error {
		if err := json.NewEncoder(out).Encode(struct {
			Type        string        `json:"type"`
			Credentials model_Session `json:"credentials"`
		}{"credentials", value}); err != nil {
			return protocol_NewError("checkpoint_failed")
		}
		var ack struct {
			OK bool `json:"ok"`
		}
		if !scanner.Scan() || len(scanner.Bytes()) > 1024 || json.Unmarshal(scanner.Bytes(), &ack) != nil || !ack.OK {
			return protocol_NewError("checkpoint_failed")
		}
		return nil
	}
	return session, save, nil
}

var checkoutCookieFamilies = []string{"__Secure-next-auth.session-token", "__Secure-platform.session-token"}

func checkoutCookieFamily(name string) string {
	for _, base := range checkoutCookieFamilies {
		if name == base {
			return base
		}
		if suffix, found := strings.CutPrefix(name, base+"."); found {
			if n, err := strconv.Atoi(suffix); err == nil && n >= 0 && n < 100 && strconv.Itoa(n) == suffix {
				return base
			}
		}
	}
	return ""
}

// Replace a rotated login-cookie family as a whole, including transitions
// between single and chunked tokens. Honour upstream cookie deletion.
func checkoutAbsorbCookies(raw string, response *protocol_HTTPResponse) string {
	jar := map[string]string{}
	for _, c := range (&http.Request{Header: http.Header{"Cookie": {raw}}}).Cookies() {
		jar[c.Name] = c.Value
	}
	parsed := (&http.Response{Header: response.Headers}).Cookies()
	deleted := func(c *http.Cookie) bool {
		return c.MaxAge < 0 || (!c.Expires.IsZero() && c.Expires.Before(time.Now())) || c.Value == ""
	}
	for _, c := range parsed {
		if family := checkoutCookieFamily(c.Name); family != "" && !deleted(c) {
			for name := range jar {
				if checkoutCookieFamily(name) == family {
					delete(jar, name)
				}
			}
		}
	}
	for _, c := range parsed {
		if deleted(c) {
			delete(jar, c.Name)
		} else {
			jar[c.Name] = c.Value
		}
	}
	names := make([]string, 0, len(jar))
	for name := range jar {
		names = append(names, name)
	}
	sort.Strings(names)
	parts := make([]string, 0, len(names))
	for _, name := range names {
		parts = append(parts, name+"="+jar[name])
	}
	return strings.Join(parts, "; ")
}

func checkoutSessionToken(raw string) string {
	jar := map[string]string{}
	for _, c := range (&http.Request{Header: http.Header{"Cookie": {raw}}}).Cookies() {
		jar[c.Name] = c.Value
	}
	for _, base := range checkoutCookieFamilies {
		if jar[base] != "" {
			return jar[base]
		}
		var parts []string
		for i := 0; i < 100; i++ {
			part := jar[base+"."+strconv.Itoa(i)]
			if part == "" {
				break
			}
			parts = append(parts, part)
		}
		if len(parts) > 0 {
			return strings.Join(parts, "")
		}
	}
	return ""
}
