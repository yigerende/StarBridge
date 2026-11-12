"""Administrator resource operations; the imported refund protocol stays unchanged."""
import base64
import binascii
import json
import os
import secrets
import time
from urllib.parse import quote, urlencode, urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from curl_cffi import requests

from .common import BusinessError, require, now


def open_secret(label, value):
    raw = bytes(value)
    cipher = AESGCM(base64.b64decode(os.environ['MASTER_KEY']))
    return json.loads(cipher.decrypt(raw[:12], raw[12:], label.encode()))


def seal_secret(label, value):
    nonce = secrets.token_bytes(12)
    cipher = AESGCM(base64.b64decode(os.environ['MASTER_KEY']))
    return nonce + cipher.encrypt(nonce, json.dumps(value, ensure_ascii=False).encode(), label.encode())


def identity(token):
    try:
        require(isinstance(token, str) and len(token) <= 32768, '凭据格式无效')
        parts = token.split('.')
        require(len(parts) == 3, '请输入有效的 Access Token')
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
        auth = claims.get('https://api.openai.com/auth') or {}
        profile = claims.get('https://api.openai.com/profile') or {}
        require(type(claims.get('exp', 0)) in (int, float), '凭据到期时间无效')
        return {'email': str(profile.get('email') or claims.get('email') or '').strip().lower(),
                'user_id': auth.get('chatgpt_user_id') or claims.get('sub') or '',
                'team_id': auth.get('chatgpt_account_id') or '',
                'plan_type': auth.get('chatgpt_plan_type') or '', 'expires': claims.get('exp', 0)}
    except (ValueError, TypeError, AttributeError, IndexError, binascii.Error):
        raise BusinessError('无法读取 Access Token 的账号资料') from None


class UpstreamError(BusinessError):
    def __init__(self, message, http_status=0):
        super().__init__(message, 502)
        self.http_status = http_status


class TeamClient:
    def __init__(self, token, team, proxy, heartbeat=None):
        parsed = urlsplit(proxy)
        require(parsed.scheme in ('http', 'https', 'socks5', 'socks5h') and parsed.hostname,
                '请配置有效的专属代理')
        self.team, self.token, self.heartbeat = team, token, heartbeat
        self.session = requests.Session(impersonate='chrome136', trust_env=False,
                                       proxies={'http': proxy, 'https': proxy})
        self.headers = {'Authorization': 'Bearer ' + token, 'chatgpt-account-id': team,
                        'Origin': 'https://chatgpt.com', 'Referer': 'https://chatgpt.com/admin/members',
                        'Accept': 'application/json', 'oai-language': 'zh-CN'}

    def close(self):
        self.session.close()

    def request(self, method, path, body=None):
        require(path.startswith('/') and not path.startswith('//'), '请求路径无效')
        for attempt in range(3 if method == 'GET' else 1):
            if self.heartbeat:
                self.heartbeat()
            try:
                r = self.session.request(method, 'https://chatgpt.com/backend-api' + path,
                                         headers=self.headers, json=body, timeout=20, allow_redirects=False)
            except Exception:
                if method == 'GET' and attempt < 2:
                    time.sleep(.5 * (attempt + 1))
                    continue
                raise UpstreamError('上游连接未完成，请检查代理后重试') from None
            if method == 'GET' and r.status_code in (408, 429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(.5 * (attempt + 1))
                continue
            if not 200 <= r.status_code < 300:
                raise UpstreamError(f'上游返回 HTTP {r.status_code}', r.status_code)
            if not r.content and method == 'DELETE':
                return {}
            if len(r.content) > 4 * 1024 * 1024:
                raise UpstreamError('上游响应过大，未使用不完整数据')
            try:
                value = r.json()
            except ValueError:
                raise UpstreamError('上游未返回有效数据', r.status_code) from None
            if not isinstance(value, dict) or value.get('success') is False or value.get('error'):
                raise UpstreamError('上游未确认操作成功', r.status_code)
            return value

    def path(self, suffix):
        return '/accounts/' + quote(self.team, safe='') + suffix

    def collection(self, kind='users', held=False):
        rows, seen, offset = [], set(), 0
        for _ in range(400):
            params = {'offset': offset, 'limit': 25, 'query': ''}
            if kind == 'invites':
                params.update(include_pending='true', include_requests='true')
            if held:
                params['active_vacancy_hold_seat_type'] = 'prolite'
            data = self.request('GET', self.path('/' + kind) + '?' + urlencode(params))
            items = next((data[k] for k in ('items', 'users', 'members', 'invites', 'data', 'account_invites')
                          if isinstance(data.get(k), list)), None)
            require(items is not None and len(items) <= 25 and all(isinstance(r, dict) for r in items),
                    '上游列表不完整，无法确认成员状态', 502)
            signature = json.dumps(items, sort_keys=True)
            require(not items or signature not in seen, '上游重复返回同一页，无法确认完整成员列表', 502)
            seen.add(signature)
            rows.extend(items)
            offset += len(items)
            total, more = data.get('total'), data.get('has_more')
            require(total is None or type(total) is int and total >= offset, '上游成员总数无效', 502)
            require(more is None or type(more) is bool, '上游分页状态无效', 502)
            if more is True or total is not None and offset < total:
                require(bool(items) and more is not False, '上游成员分页不完整', 502)
                continue
            if more is False or total is not None or len(items) < 25:
                return rows
        raise BusinessError('上游成员超过查询上限，未使用不完整数据', 502)

    def members(self, held=False):
        result = []
        for r in self.collection(held=held):
            row = {'id': str(r.get('user_id') or r.get('id') or r.get('chatgpt_user_id') or ''),
                   'email': str(r.get('email') or r.get('email_address') or '').lower(),
                   'name': str(r.get('name') or ''), 'role': str(r.get('role') or ''),
                   'seat_type': str(r.get('seat_type') or ''),
                   'created_time': r.get('created_time') or r.get('created_at') or '',
                   'active': r.get('active') is not False and not r.get('deactivated_time'),
                   'deactivated_time': r.get('deactivated_time') or '',
                   'reclaimable_seat_type': r.get('reclaimable_seat_type') or ''}
            if not held or row['reclaimable_seat_type'] == 'prolite' and row['deactivated_time']:
                require(row['id'] and row['email'], '上游成员身份不完整，未使用该列表', 502)
                result.append(row)
        return result

    def capacity(self):
        data = self.request('GET', '/subscriptions?' + urlencode({'account_id': self.team}))
        rows = data.get('seat_capacity')
        require(isinstance(rows, list), '上游未返回席位容量', 502)
        result = {'checked_at': now().isoformat()}
        for typ, label in (('default', 'standard'), ('prolite', 'premium')):
            candidates = [r for r in rows if isinstance(r, dict) and r.get('type') == typ]
            require(len(candidates) == 1, '上游席位类型不完整', 502)
            row = candidates[0]
            require(all(type(row.get(k)) is int and row[k] >= 0 for k in ('paid', 'held', 'available'))
                    and row['paid'] >= row['held'], '上游席位数量不完整', 502)
            result[label] = {'total': row['paid'] - row['held'], 'held': row['held'], 'remaining': row['available']}
        return result

    def plan(self):
        me = self.request('GET', '/me')
        data = self.request('GET', '/accounts/check/v4-2023-04-27?timezone_offset_min=-480')
        accounts = data.get('accounts')
        require(isinstance(accounts, dict), '上游未返回套餐资料', 502)
        matches = [v for k, v in accounts.items() if isinstance(v, dict) and
                   (k == self.team or (v.get('account') or {}).get('account_id') == self.team)]
        require(bool(matches), '上游未返回目标团队的套餐，未使用其他空间的数据', 502)
        item = matches[0]
        account, entitlement = item.get('account') or {}, item.get('entitlement') or {}
        return {'email': str(me.get('email') or '').lower(), 'plan_type': account.get('plan_type') or '',
                'subscription_plan': entitlement.get('subscription_plan') or '',
                'has_active_subscription': entitlement.get('has_active_subscription'),
                'expires_at': entitlement.get('expires_at'), 'renews_at': entitlement.get('renews_at'),
                'billing_period': entitlement.get('billing_period'), 'billing_currency': entitlement.get('billing_currency'),
                'checked_at': now().isoformat()}

    def refresh(self, refresh_token):
        if self.heartbeat:
            self.heartbeat()
        try:
            r = self.session.post('https://auth.openai.com/oauth/token', data={
                'grant_type': 'refresh_token', 'client_id': 'app_EMoamEEZ73f0CkXaXp7hrann',
                'refresh_token': refresh_token, 'scope': 'openid profile email'}, timeout=20, allow_redirects=False)
            if not 200 <= r.status_code < 300:
                raise UpstreamError(f'凭据刷新返回 HTTP {r.status_code}', r.status_code)
            value = r.json()
            require(isinstance(value, dict) and isinstance(value.get('access_token'), str), '刷新未返回 AT', 502)
            return value
        except BusinessError:
            raise
        except Exception:
            raise UpstreamError('凭据刷新结果未确认，请核对后再操作') from None
