"""Read-only registration observer; captures complete sessions after successful work.

No engine patches, account/ledger writes, registration retries or OAuth calls.
Only GET /api/auth/session is retried; its output has its own encrypted storage.
"""
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from . import db
from .common import BusinessError, decrypt, encrypt, require, uid

log = logging.getLogger('starbridge')
SESSION_URL = 'https://chatgpt.com/api/auth/session'
MAX_SESSION_BYTES = 1024 * 1024
MAX_ATTEMPTS = 3
SAFE_ID = re.compile(r'^[a-zA-Z0-9_-]{1,160}$')


class CaptureFailure(Exception):
    def __init__(self, code, retry=False):
        self.code, self.retry = code, retry
        super().__init__(code)


def timestamp(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def runtime_directory():
    # Container mount, or an explicitly configured isolated local runtime.
    return Path(os.getenv('REGISTRATION_RUNTIME_DIR', str(db.ROOT / 'registration' / '.runtime')))


def engine_access():
    url = os.getenv('WORKER_URL', '').rstrip('/')
    parsed = urlsplit(url)
    require(parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username
            and not parsed.password and parsed.path in ('', '/') and not parsed.query
            and not parsed.fragment, '注册服务地址未配置', 503)
    secrets = json.loads((runtime_directory() / 'platform-secrets.json').read_text('utf-8'))
    secret = secrets.get('internal', '')
    require(isinstance(secret, str) and len(secret) >= 32, '注册服务暂不可用', 503)
    return url, secret


async def read_json(client, url, *, headers=None, limit=MAX_SESSION_BYTES):
    """Bound both elapsed time and response size; never follow credential redirects."""
    async with asyncio.timeout(20):
        async with client.stream('GET', url, headers=headers) as response:
            if response.status_code != 200:
                raise CaptureFailure('http_' + str(response.status_code),
                                     response.status_code in (408, 429, 500, 502, 503, 504))
            content = bytearray()
            async for part in response.aiter_bytes(chunk_size=65536):
                content.extend(part)
                if len(content) > limit:
                    raise CaptureFailure('response_too_large')
            try:
                value = json.loads(content)
            except (ValueError, UnicodeError):
                raise CaptureFailure('invalid_json') from None
            if not isinstance(value, dict):
                raise CaptureFailure('invalid_json')
            return value


async def engine_asset(asset_id, *, client_factory=httpx.AsyncClient):
    if not SAFE_ID.fullmatch(str(asset_id)):
        raise CaptureFailure('invalid_asset')
    url, secret = await asyncio.to_thread(engine_access)
    async with client_factory(timeout=httpx.Timeout(12, connect=5), trust_env=False,
                              follow_redirects=False) as client:
        value = await read_json(client, url + '/api/v1/session-assets/' + quote(asset_id, safe=''),
                                headers={'Authorization': 'Bearer ' + secret}, limit=4 * MAX_SESSION_BYTES)
    if value.get('id') != asset_id:
        raise CaptureFailure('asset_mismatch')
    return value


def session_cookie(token):
    # The engine returns the merged Cookie value, including tokens originally chunked.
    if not isinstance(token, str) or not token or len(token) > 32768 or re.search(r'[\s;\x00-\x1f\x7f]', token):
        raise CaptureFailure('missing_session_token')
    return '__Secure-next-auth.session-token=' + token


def validate_session(value, email):
    if value.get('error'):
        raise CaptureFailure('invalid_session')
    if not isinstance(value.get('accessToken'), str) or not value['accessToken'].strip():
        raise CaptureFailure('missing_access_token')
    user = value.get('user')
    if not isinstance(user, dict) or str(user.get('email', '')).strip().casefold() != email.casefold():
        raise CaptureFailure('account_mismatch')
    expires = timestamp(value.get('expires'))
    if not expires or expires <= datetime.now(timezone.utc):
        raise CaptureFailure('invalid_expiry')
    return value


async def fetch_session(asset, *, client_factory=httpx.AsyncClient):
    email = str(asset.get('email', '')).strip()
    if not email or any(c in email for c in '\r\n'):
        raise CaptureFailure('invalid_email')
    cookie = session_cookie(asset.get('sessionToken'))
    proxy = asset.get('registrationProxyUrl') or ''
    if proxy:
        parsed = urlsplit(proxy)
        if parsed.scheme not in ('http', 'https', 'socks5', 'socks5h') or not parsed.hostname:
            raise CaptureFailure('invalid_proxy')
    elif asset.get('sourceProxy'):
        # An expired/missing binding must not silently switch to the server's IP.
        raise CaptureFailure('missing_proxy')
    async with client_factory(proxy=proxy or None, timeout=httpx.Timeout(12, connect=5),
                              trust_env=False, follow_redirects=False) as client:
        value = await read_json(client, SESSION_URL, headers={'Accept': 'application/json', 'Cookie': cookie})
    return validate_session(value, email)


def enqueue(key, asset_id, completed_at, email=''):
    if not SAFE_ID.fullmatch(str(asset_id)):
        return
    db.execute("""INSERT INTO registration_session_captures(completion_key,asset_id,email,completed_at)
        VALUES($1,$2,$3,$4) ON CONFLICT(completion_key) DO NOTHING""", key, asset_id, email, completed_at)


def discover_deliveries():
    if not db.scalar("SELECT to_regclass('platform_accounts')"):
        return
    # A delivery is committed only after standard registration, or registration + OAuth.
    # Start at feature installation, rather than refreshing historical customer accounts.
    rows = db.all("""SELECT a.id,a.asset_id,a.email,a.created_at FROM platform_accounts a
        WHERE a.created_at >= (SELECT enabled_at FROM registration_session_capture_start WHERE id)
        AND NOT EXISTS(SELECT 1 FROM registration_session_captures s WHERE s.completion_key='delivery:'||a.id)
        ORDER BY a.created_at LIMIT 100""")
    for row in rows:
        enqueue('delivery:' + row['id'], row['asset_id'], row['created_at'], row['email'])


def completed_assets(state, since):
    """Observe successful jobs in original-console runs, never intermediate OAuth work."""
    if state.get('customerExecution'):
        return []  # Customer deliveries use the authoritative transaction above.
    plan = state.get('runPlan') or {}
    kind = state.get('taskKind') or plan.get('taskKind')
    if kind not in ('register_session', 'register_oauth', 'oauth'):
        return []
    if kind == 'oauth' and (plan.get('oauthSource') or {}).get('loginOnly'):
        return []
    oauth = kind in ('register_oauth', 'oauth') or plan.get('postRegistrationOAuth') is True
    result = []
    for job in state.get('jobs', []):
        completed = timestamp(job.get('completedAt'))
        asset_id = job.get('sessionAssetId') or job.get('assetId')
        if job.get('status') != 'complete' or not completed or completed < since or not asset_id:
            continue
        if oauth and (not job.get('cpaPath') or job.get('cpaSuperseded')):
            continue
        if not state.get('runId') or not job.get('id'):
            continue
        result.append(('engine:' + str(state['runId']) + ':' + str(job['id']), asset_id, completed))
    return result


class RunObserver:
    """Read changed checkpoints from the existing shared volume; never write them."""
    def __init__(self):
        self.seen = {}

    def discover(self):
        root = runtime_directory().resolve()
        config = json.loads((root / 'gpt-re.config.json').read_text('utf-8'))
        relative = Path(config['output']['stateDir'])
        directory = (root.parent / relative).resolve()
        if not directory.is_relative_to(root):
            raise CaptureFailure('state_directory_outside_runtime')
        since = db.scalar('SELECT enabled_at FROM registration_session_capture_start WHERE id')
        live = set()
        for file in directory.glob('run-*.json'):
            live.add(file)
            stat = file.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if self.seen.get(file) == signature or stat.st_mtime < since.timestamp():
                continue
            if stat.st_size > 64 * MAX_SESSION_BYTES:
                continue
            try:
                state = json.loads(file.read_text('utf-8'))
                for key, asset_id, completed in completed_assets(state, since):
                    enqueue(key, asset_id, completed)
            except (OSError, ValueError):
                continue  # An atomic engine rewrite can be retried on the next scan.
            self.seen[file] = signature
        self.seen = {key: value for key, value in self.seen.items() if key in live}


def claim():
    lease = uid()
    return db.one("""WITH next AS (
        SELECT completion_key FROM registration_session_captures
        WHERE (state='queued' AND available_at<=now()) OR (state='running' AND lease_until<now())
        ORDER BY completed_at FOR UPDATE SKIP LOCKED LIMIT 1
      ) UPDATE registration_session_captures s SET state='running',attempts=attempts+1,
        lease_token=$1,lease_until=now()+interval '90 seconds'
        FROM next WHERE s.completion_key=next.completion_key RETURNING s.*""", lease)


def save_result(job, email, value):
    # Keep the full response, including fields the UI does not currently know about.
    sealed = encrypt(json.dumps(value, ensure_ascii=False, separators=(',', ':')))
    db.execute("""UPDATE registration_session_captures SET state='ready',email=$3,
        session_cipher=$4,fetched_at=now(),error_code='',lease_token=NULL,lease_until=NULL
        WHERE completion_key=$1 AND state='running' AND lease_token=$2""",
        job['completion_key'], job['lease_token'], email, sealed)


def fail_result(job, error):
    retry = error.retry and job['attempts'] < MAX_ATTEMPTS
    db.execute("""UPDATE registration_session_captures SET state=$3,error_code=$4,
        available_at=now()+$5*interval '1 second',lease_token=NULL,lease_until=NULL
        WHERE completion_key=$1 AND state='running' AND lease_token=$2""",
        job['completion_key'], job['lease_token'], 'queued' if retry else 'failed',
        error.code, 5 if job['attempts'] == 1 else 20)


async def capture(job):
    try:
        if job['attempts'] > MAX_ATTEMPTS:
            raise CaptureFailure('attempts_exhausted')
        asset = await engine_asset(job['asset_id'])
        email = str(asset.get('email', '')).strip()
        if job['email'] and email.casefold() != job['email'].casefold():
            raise CaptureFailure('account_mismatch')
        value = await fetch_session(asset)
        await asyncio.to_thread(save_result, job, email, value)
    except asyncio.CancelledError:
        # The durable lease expires after a process interruption.
        raise
    except (httpx.TransportError, TimeoutError):
        await asyncio.to_thread(fail_result, job, CaptureFailure('network_error', True))
    except CaptureFailure as error:
        await asyncio.to_thread(fail_result, job, error)
    except (OSError, BusinessError):
        await asyncio.to_thread(fail_result, job, CaptureFailure('engine_unavailable', True))
    except Exception:
        await asyncio.to_thread(fail_result, job, CaptureFailure('capture_error', True))


async def run():
    observer = RunObserver()
    active = set()
    discovery_at = 0
    try:
        while True:
            for task in list(active):
                if task.done():
                    try:
                        task.result()
                    except Exception:
                        log.warning('Registration Session persistence will retry after its lease expires')
                    active.remove(task)
            if time.monotonic() - discovery_at >= 5:
                discovery_at = time.monotonic()
                for discover in (discover_deliveries, observer.discover):
                    try:
                        await asyncio.to_thread(discover)
                    except Exception:
                        log.warning('Registration Session discovery unavailable; retrying')
            try:
                while len(active) < 2:
                    job = await asyncio.to_thread(claim)
                    if not job:
                        break
                    active.add(asyncio.create_task(capture(job)))
            except Exception:
                log.warning('Registration Session queue unavailable; retrying')
            if active:
                await asyncio.wait(active, timeout=1, return_when=asyncio.FIRST_COMPLETED)
            else:
                await asyncio.sleep(1)
    finally:
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)


def start():
    if os.getenv('XUFU_INTEGRATION') == '1' and os.getenv('WORKER_URL'):
        return asyncio.create_task(run(), name='registration-sessions')
    return None


async def stop(task):
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def attach_sessions(values):
    """Call only after the original API has authorized every delivery in values."""
    ids = list({v.get('asset_id') for v in values if v.get('asset_id')})
    rows = db.all("""SELECT a.asset_id,a.email,
        COALESCE(s.state,CASE WHEN a.created_at>=c.enabled_at THEN 'queued' ELSE 'unavailable' END) AS state,
        s.email AS captured_email,s.session_cipher,s.fetched_at FROM platform_accounts a
        CROSS JOIN registration_session_capture_start c
        LEFT JOIN LATERAL (SELECT email,state,session_cipher,fetched_at FROM registration_session_captures
          WHERE asset_id=a.asset_id ORDER BY completed_at DESC,created_at DESC LIMIT 1) s ON true
        WHERE a.asset_id=ANY($1::text[])""", ids) if ids else []
    by_id = {row['asset_id']: row for row in rows}
    result = []
    for original in values:
        value = {key: v for key, v in original.items() if key != 'session_token'}
        row = by_id.get(value.get('asset_id'))
        value.update(session=None, session_status='unavailable', session_fetched_at=None)
        if row and (not row['email'] or row['email'].casefold() == str(value.get('email', '')).casefold()):
            value['session_status'] = 'pending' if row['state'] in ('queued', 'running') else row['state']
            if row['state'] == 'ready' and str(row['captured_email']).casefold() == row['email'].casefold():
                value['session'] = json.loads(decrypt(row['session_cipher']))
                value['session_fetched_at'] = row['fetched_at'].isoformat()
            elif row['state'] == 'ready':
                value['session_status'] = 'failed'
        result.append(value)
    return result


def session_export(values):
    missing = sum(not isinstance(value.get('session'), dict) for value in values)
    require(not missing, f'所选账号中有 {missing} 个尚未获取完整 Session，请稍后重试或联系管理员；本次未导出任何账号', 409)
    return {'filename': 'starbridge-email-session.txt', 'content': ''.join(
        str(value['email']) + '----' + json.dumps(value['session'], ensure_ascii=False, separators=(',', ':')) + '\n'
        for value in values)}
