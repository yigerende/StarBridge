"""Durable, private login-state checkpoints before checkout/payment submission."""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from http.cookies import SimpleCookie

from . import db
from .checkout_errors import CheckoutError
from .common import decrypt, encrypt, now

COOKIE_NAME = re.compile(r'^__Secure-(?:next-auth|platform)\.session-token(?:\.(?:0|[1-9][0-9]?))?$')
COOKIE_BASES = ('__Secure-next-auth.session-token', '__Secure-platform.session-token')


def session_token(cookies):
    """Cookies are authoritative; reconstruct numbered chunks in numeric order."""
    for base in COOKIE_BASES:
        if cookies.get(base):
            return cookies[base]
        chunks = {int(k[len(base)+1:]): v for k, v in cookies.items()
                  if k.startswith(base+'.') and COOKIE_NAME.fullmatch(k) and v}
        if chunks and sorted(chunks) == list(range(len(chunks))):
            return ''.join(chunks[n] for n in range(len(chunks)))
    return ''


def refreshed_credentials(previous, value):
    if not isinstance(value, dict):
        raise CheckoutError('checkpoint_failed')
    fields = {'access_token': 32768, 'session_token': 32768, 'cookies': 65536,
              'account_id': 256, 'email': 320}
    for key, limit in fields.items():
        field = value.get(key)
        if not isinstance(field, str) or not field or len(field) > limit or any(ord(c) < 32 for c in field):
            raise CheckoutError('checkpoint_failed')
    if (value['account_id'] != previous.get('account_id')
            or value['email'].casefold() != str(previous.get('email') or '').casefold()):
        raise CheckoutError('account_mismatch')
    jar = SimpleCookie()
    try:
        jar.load(value['cookies'])
    except Exception:
        raise CheckoutError('checkpoint_failed') from None
    cookies = {k: v.value for k, v in jar.items() if COOKIE_NAME.fullmatch(k) and v.value}
    token = session_token(cookies)
    if not token or token != value['session_token']:
        raise CheckoutError('checkpoint_failed')
    result = dict(previous, access_token=value['access_token'], session_token=token, cookies=cookies,
                  refreshed_at=now().isoformat())
    # Preserve other order credentials (including Team promotion), but don't leave
    # stale AT metadata when a new token is installed.
    if value['access_token'] != previous.get('access_token'):
        result.pop('token_expires_at', None)
    try:
        part = value['access_token'].split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
        auth = claims.get('https://api.openai.com/auth') or {}
        profile = claims.get('https://api.openai.com/profile') or {}
        if (auth.get('chatgpt_account_id') and auth['chatgpt_account_id'] != previous['account_id']
                or profile.get('email') and profile['email'].casefold() != previous['email'].casefold()):
            raise CheckoutError('account_mismatch')
        if claims.get('exp'):
            result['token_expires_at'] = datetime.fromtimestamp(claims['exp'], timezone.utc).isoformat()
    except (ValueError, KeyError, IndexError, TypeError, OverflowError, AttributeError):
        pass
    return result


def saver(job, credentials):
    """ACK only after commit; stale workers cannot overwrite a newer login state."""
    expected = job['credentials_enc']

    def save(value):
        nonlocal expected
        updated = refreshed_credentials(credentials, value)
        try:
            with db.transaction() as q:
                q.execute("SET LOCAL lock_timeout='5s'")
                q.execute("SET LOCAL statement_timeout='5s'")
                row = q.one('SELECT o.status,o.deleted_at,o.workflow,s.subscription_flow,s.trace_round,'
                            's.credentials_enc,s.credentials_expires_at,s.payment_submit_started_at,s.paid_at '
                            'FROM orders o JOIN subscription_runs s ON s.order_id=o.id '
                            'WHERE o.id=$1 FOR UPDATE OF o,s', job['id'])
                if (not row or row['status'] != 'processing' or row['deleted_at']
                        or row['workflow'] not in ('subscription', 'link')
                        or row['subscription_flow'] != 'cslive_go_direct_pay'
                        or row['trace_round'] != job.get('trace_round')
                        or row['credentials_enc'] != expected
                        or not row['credentials_expires_at'] or row['credentials_expires_at'] <= now()
                        or row['payment_submit_started_at'] or row['paid_at']):
                    raise CheckoutError('checkpoint_failed')
                # Compare the durable identity too, independently of the mutable caller.
                stored = json.loads(decrypt(row['credentials_enc']))
                if stored['account_id'] != updated['account_id'] or stored['email'].casefold() != updated['email'].casefold():
                    raise CheckoutError('account_mismatch')
                encoded = encrypt(json.dumps(updated, ensure_ascii=True))
                q.execute('UPDATE subscription_runs SET credentials_enc=$2 WHERE order_id=$1', job['id'], encoded)
        except CheckoutError:
            raise
        except Exception:
            raise CheckoutError('checkpoint_failed') from None
        expected = encoded
        credentials.clear()
        credentials.update(updated)
    return save


def restore_order_credentials(checkpoint, encrypted):
    """Progress snapshots are not an alternative source of order login state."""
    try:
        latest = json.loads(decrypt(encrypted))
        previous = checkpoint.get('credentials') or {}
        if (not latest.get('access_token') or latest['account_id'] != checkpoint['account_id']
                or str(latest.get('email') or '').casefold() != str(previous.get('email') or '').casefold()):
            raise CheckoutError('account_mismatch')
    except CheckoutError:
        raise
    except Exception:
        raise CheckoutError('checkpoint_failed') from None
    checkpoint['credentials'] = latest
