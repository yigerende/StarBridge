package main

import (
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"
)

func authCookieFamily(name string) string {
	for _, base := range []string{"__Secure-next-auth.session-token", "__Secure-platform.session-token"} {
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

func absorbSessionCookies(raw string, response *protocol_HTTPResponse) string {
	values := map[string]string{}
	for _, c := range (&http.Request{Header: http.Header{"Cookie": {raw}}}).Cookies() {
		values[c.Name] = c.Value
	}
	parsed := (&http.Response{Header: response.Headers}).Cookies()
	deleted := func(c *http.Cookie) bool {
		return c.MaxAge < 0 || (!c.Expires.IsZero() && c.Expires.Before(time.Now())) || c.Value == ""
	}
	for _, c := range parsed {
		if family := authCookieFamily(c.Name); family != "" && !deleted(c) {
			for name := range values {
				if authCookieFamily(name) == family {
					delete(values, name)
				}
			}
		}
	}
	for _, c := range parsed {
		if deleted(c) {
			delete(values, c.Name)
		} else {
			values[c.Name] = c.Value
		}
	}
	names := make([]string, 0, len(values))
	for name := range values {
		names = append(names, name)
	}
	sort.Strings(names)
	parts := make([]string, 0, len(names))
	for _, name := range names {
		parts = append(parts, name+"="+values[name])
	}
	return strings.Join(parts, "; ")
}
