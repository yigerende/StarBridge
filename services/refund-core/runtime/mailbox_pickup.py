"""Order-scoped mailbox access, using Space's vendored parsers.

No login cookies or business proxy are sent to the mailbox. Every connection
resolves and pins a public IP; redirects receive the same checks.
"""
import http.client
import ipaddress
import json
import re
import socket
import time
import types
import urllib.error
import urllib.parse
import urllib.request

from manager_oauth import upstream

MAX_BODY = 2 * 1024 * 1024
WAIT_SECONDS = 120
INTERVAL_SECONDS = 5


class MailboxError(Exception):
    code = "email_code_unavailable"


class UnsafeMailbox(MailboxError):
    code = "email_code_address_blocked"


def validate_url(value):
    try:
        p = urllib.parse.urlsplit(value)
        if (len(value) > 4096 or re.search(r"[\x00-\x20\x7f]", value)
                or p.scheme not in {"http", "https"} or not p.hostname
                or p.username is not None or p.password is not None
                or p.port == 0 or "%" in p.hostname):
            raise ValueError()
        host = p.hostname.rstrip('.').lower()
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
            raise ValueError()
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and not public_ip(ip):
            raise ValueError()
        return p
    except (ValueError, TypeError):
        raise UnsafeMailbox('邮箱接码地址不可访问') from None


def public_ip(ip):
    return ip.is_global and not ip.is_multicast and not ip.is_reserved and not getattr(ip, 'ipv4_mapped', None)


def connect_public(host, port, timeout):
    rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not rows or any(not public_ip(ipaddress.ip_address(row[4][0])) for row in rows):
        raise UnsafeMailbox('邮箱接码地址不可访问')
    # Pin the validated address. A second DNS lookup cannot redirect to LAN.
    family, kind, protocol, _, address = rows[0]
    sock = socket.socket(family, kind, protocol)
    try:
        sock.settimeout(timeout)
        sock.connect(address)
        return sock
    except BaseException:
        sock.close()
        raise


class PublicHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = connect_public(self.host, self.port, self.timeout)


class PublicHTTPS(http.client.HTTPSConnection):
    def connect(self):
        sock = connect_public(self.host, self.port, self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(PublicHTTP, req)


class HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(PublicHTTPS, req, context=self._context)


class Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class BoundedResponse:
    def __init__(self, response):
        self.response = response
        self.remaining = MAX_BODY

    def read(self, size=-1):
        value = self.response.read(min(size, self.remaining + 1) if size >= 0 else self.remaining + 1)
        self.remaining -= len(value)
        if self.remaining < 0:
            raise MailboxError('邮箱响应过大')
        return value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.response.close()


class MailboxReader:
    def __init__(self, email, url):
        validate_url(url)
        self.email, self.url = email, url
        self.deadline = time.monotonic() + WAIT_SECONDS
        self.requests = 0
        # Independent function globals retain the original parsers unchanged.
        # No module-global monkeypatch can affect another login.
        space = dict(vars(upstream))
        facade = types.SimpleNamespace(**vars(urllib.request))
        facade.build_opener = self.build_opener
        space['urllib'] = types.SimpleNamespace(parse=urllib.parse, request=facade)
        space['http_request_text'] = self.text
        space['http_request_json'] = self.json
        space['_PICKUP_BAD_HOSTS'] = {}
        names = {'fetch_directurl_messages', 'fetch_mailtoken_messages'}
        names.update(n for n in space if n.startswith(('_fetch_', '_weimail_', '_msgviewer_', '_thefindnet_', '_aisvip_', '_api798_')))
        for name in names:
            f = space[name]
            if isinstance(f, types.FunctionType):
                copy = types.FunctionType(f.__code__, space, name, f.__defaults__, f.__closure__)
                copy.__kwdefaults__ = f.__kwdefaults__
                space[name] = copy
        space['_PICKUP_ADAPTERS'] = [
            ('api798', space['_api798_match'], space['_fetch_api798_messages']),
            ('weimail', lambda url, account: space['_weimail_api_url'](url), space['_fetch_weimail_messages']),
            ('msgviewer', lambda url, account: space['_msgviewer_api'](url), space['_fetch_msgviewer_messages']),
            ('thefindnet', space['_thefindnet_match'], space['_fetch_thefindnet_messages']),
            ('aisvip', space['_aisvip_match'], space['_fetch_aisvip_messages']),
        ]
        self.fetch = space['fetch_mailtoken_messages' if urllib.parse.urlsplit(url).fragment else 'fetch_directurl_messages']

    def build_opener(self, *handlers):
        reader = self
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), HTTPHandler(), HTTPSHandler(), Redirects(), *handlers)

        class Opener:
            def open(self, request, timeout=20):
                validate_url(request.full_url if isinstance(request, urllib.request.Request) else request)
                remaining = reader.deadline - time.monotonic()
                if remaining <= 0 or reader.requests >= 30:
                    raise MailboxError('邮箱取码超时')
                reader.requests += 1
                return BoundedResponse(opener.open(request, timeout=min(timeout, 15, remaining)))
        return Opener()

    def text(self, url, *, method='GET', headers=None, timeout=20, **kwargs):
        request = urllib.request.Request(url, method=method, headers={
            'User-Agent': upstream.DEFAULT_HTTP_HEADERS['User-Agent'], 'Cache-Control': 'no-cache', **(headers or {})})
        with self.build_opener().open(request, timeout=timeout) as response:
            return response.read().decode('utf-8', 'replace')

    def json(self, url, *, method='GET', json_data=None, headers=None, timeout=20, **kwargs):
        data = json.dumps(json_data).encode() if json_data is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={
            'User-Agent': upstream.DEFAULT_HTTP_HEADERS['User-Agent'], 'Cache-Control': 'no-cache',
            'Content-Type': 'application/json', **(headers or {})})
        with self.build_opener().open(request, timeout=timeout) as response:
            value = json.loads(response.read().decode('utf-8', 'replace'))
        return value if isinstance(value, dict) else {'data': value}

    def next_code(self, since, used):
        while time.monotonic() < self.deadline and self.requests < 30:
            try:
                messages = self.fetch({'email': self.email, 'pickup_url': self.url}, limit=5)
                code = upstream.find_latest_code(messages, after_ts=since, exclude_codes=used)
                if code:
                    return code
            except Exception as exc:
                # Do not let mailbox errors trigger child-proxy cooling or
                # expose the credential URL through exception chaining.
                current = exc
                for _ in range(8):
                    if isinstance(current, UnsafeMailbox):
                        raise UnsafeMailbox('邮箱接码地址不可访问') from None
                    current = getattr(current, '__cause__', None)
                    if current is None:
                        break
            remaining = self.deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(INTERVAL_SECONDS, remaining))
        raise MailboxError('未获取到可用的邮箱验证码') from None
