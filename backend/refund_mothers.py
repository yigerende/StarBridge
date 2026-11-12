"""Mother management on StarBridge's existing identity, database and encryption."""
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from . import db
from .common import administrator, audit, body, config, encrypt, decrypt, now, require, uid, email_valid, parsedate
from .listing import page_rows
from .refund_mother_upstream import TeamClient, UpstreamError, identity, open_secret, seal_secret

router = APIRouter(prefix='/api/admin/refund/mother-management', dependencies=[Depends(administrator)])
LOCK = 718374023
DEFAULTS = {'auto_refresh': True}


def mother_row(id, q=db):
    row = q.one('SELECT * FROM refund_mothers WHERE id=$1 AND deleted_at IS NULL', id)
    require(row, '母号不存在', 404)
    return row


def proxy_url(id, target='mother', q=db):
    row = q.one("SELECT secret FROM refund_proxies WHERE id=$1 AND deleted_at IS NULL AND enabled AND target IN ('both',$2)", id, target)
    require(row, '请先配置已启用的' + ('母号专属' if target == 'mother' else '子号') + '代理')
    return open_secret('proxy:' + id, row['secret'])


def active_refund(team, q=db):
    return q.scalar("""SELECT EXISTS(SELECT 1 FROM refund_execution e JOIN refund_mothers m ON m.id=e.mother_id
        WHERE m.team_id=$1 AND NOT e.released AND e.mother_released_at IS NULL)""", team)


def active_child(email, q=db):
    return q.scalar("SELECT EXISTS(SELECT 1 FROM refunds WHERE lower(email)=lower($1) AND status IN ('queued','processing','verifying','attention'))", email)


@contextmanager
def team_operation(id, child_email=''):
    token = uid()
    with db.transaction() as q:
        q.execute('SELECT pg_advisory_xact_lock($1)', LOCK)
        mother = mother_row(id, q)
        require(not active_refund(mother['team_id'], q), '母号正在办理代退，请等待当前申请结束', 409)
        claimed = q.one("""INSERT INTO refund_mother_leases(team_id,token,expires_at) VALUES($1,$2,now()+interval '90 seconds')
          ON CONFLICT(team_id) DO UPDATE SET token=excluded.token,expires_at=excluded.expires_at
          WHERE refund_mother_leases.expires_at<now() RETURNING token""", mother['team_id'], token)
        require(claimed, '该团队正在执行管理操作，请稍后重试', 409)
        if child_email:
            require(not active_child(child_email, q), '该子号正在办理代退', 409)
            require(q.one("""INSERT INTO refund_member_leases(email,token,expires_at) VALUES($1,$2,now()+interval '90 seconds')
              ON CONFLICT(email) DO UPDATE SET token=excluded.token,expires_at=excluded.expires_at
              WHERE refund_member_leases.expires_at<now() RETURNING token""", child_email, token), '该子号正在执行管理操作', 409)
    def heartbeat():
        require(db.execute("UPDATE refund_mother_leases SET expires_at=now()+interval '90 seconds' WHERE team_id=$1 AND token=$2 AND expires_at>now()", mother['team_id'], token),
                '管理操作已超时，请刷新后核实结果', 409)
        if child_email:
            require(db.execute("UPDATE refund_member_leases SET expires_at=now()+interval '90 seconds' WHERE email=$1 AND token=$2 AND expires_at>now()", child_email, token), '子号管理操作已超时', 409)
    try:
        yield mother, heartbeat
    finally:
        db.execute('DELETE FROM refund_mother_leases WHERE team_id=$1 AND token=$2', mother['team_id'], token)
        if child_email:
            db.execute('DELETE FROM refund_member_leases WHERE email=$1 AND token=$2', child_email, token)


def refresh_locked(mother, heartbeat, force=False):
    expires = mother['token_expires_at']
    if not force and (not mother['refresh_secret'] or expires is None or expires > now() + timedelta(minutes=5)):
        return mother
    require(mother['refresh_secret'], '未保存 Refresh Token，请重新录入凭据')
    require(not mother['management_json'].get('refresh_pending'), '上次凭据刷新结果不明，请重新录入有效 Session / AT')
    if mother['last_refreshed_at'] and mother['last_refreshed_at'] > now() - timedelta(seconds=60):
        return mother
    db.execute("UPDATE refund_mothers SET refresh_attempt_at=now(),management_json=management_json||'{\"refresh_pending\":true}'::jsonb WHERE id=$1", mother['id'])
    at = open_secret('mother:' + mother['id'], mother['secret'])
    rt = open_secret('mother-refresh:' + mother['id'], mother['refresh_secret'])
    client = TeamClient(at, mother['team_id'], proxy_url(mother['proxy_id']), heartbeat)
    try:
        try:
            result = client.refresh(rt)
        except UpstreamError as e:
            if e.http_status in (400, 401, 403, 429):
                db.execute("UPDATE refund_mothers SET management_json=management_json-'refresh_pending' WHERE id=$1", mother['id'])
            raise
        claims = identity(result['access_token'])
        require(claims['email'] == mother['email'].lower() and claims['team_id'] == mother['team_id'],
                '刷新返回的账号或团队不匹配，未替换原凭据', 502)
        expiry = datetime.fromtimestamp(claims['expires'], timezone.utc) if type(claims['expires']) in (int, float) and 0 < claims['expires'] < 253402300800 else None
        require(expiry and expiry > now(), '刷新未返回有效的到期时间', 502)
        with db.transaction() as q:
            q.execute('SELECT pg_advisory_xact_lock($1)', LOCK)
            heartbeat()
            require(q.execute("""UPDATE refund_mothers SET secret=$2,refresh_secret=$3,token_expires_at=$4,
              plan_type=$5,last_refreshed_at=now(),management_json=management_json-'refresh_pending',auth_failed_fingerprint=NULL,check_json=NULL,updated_at=now()
              WHERE id=$1 AND secret=$6 AND team_id=$7 AND deleted_at IS NULL""", mother['id'],
              seal_secret('mother:' + mother['id'], result['access_token']),
              seal_secret('mother-refresh:' + mother['id'], result.get('refresh_token') or rt),
              expiry, claims['plan_type'], mother['secret'], mother['team_id']), '母号资料已改变，请重新核实', 409)
        return mother_row(mother['id'])
    finally:
        client.close()


@contextmanager
def mother_client(id, force_refresh=False, child_email=''):
    with team_operation(id, child_email) as (mother, heartbeat):
        if force_refresh or settings()['auto_refresh']:
            mother = refresh_locked(mother, heartbeat, force_refresh)
        client = TeamClient(open_secret('mother:' + id, mother['secret']), mother['team_id'], proxy_url(mother['proxy_id']), heartbeat)
        try:
            yield mother, client
        finally:
            client.close()


@contextmanager
def readonly_client(id):
    mother = mother_row(id)
    # Reading a roster never reserves a team or changes credentials in use.
    client = TeamClient(open_secret('mother:' + id, mother['secret']), mother['team_id'], proxy_url(mother['proxy_id']))
    try:
        yield mother, client
    finally:
        client.close()


def settings():
    saved = config('refund_mother_management')
    return {key: saved.get(key, value) for key, value in DEFAULTS.items()}


@router.get('/settings')
def get_settings():
    return settings()


@router.put('/settings')
async def put_settings(request: Request):
    value = await body(request)
    require(isinstance(value, dict) and set(value) <= set(DEFAULTS), '设置字段无效')
    value = {**settings(), **value}
    require(type(value['auto_refresh']) is bool, '自动刷新设置无效')
    with db.transaction() as q:
        q.execute("UPDATE settings SET value=$1 WHERE key='refund_mother_management'", value)
        audit(q, request, 'refund.mothers.settings', 'refund', '更新母号自动刷新设置')
    return value


@router.get('/summary')
def summary():
    rows = db.all("""SELECT m.id,m.name,m.email,m.team_id,m.management_json management_json,m.last_refreshed_at,m.check_json,
      count(e.refund_id)::int AS handled,count(e.refund_id) FILTER(WHERE r.status='succeeded')::int AS completed,
      COALESCE(sum(r.fee) FILTER(WHERE r.fee_status='settled'),0)::bigint AS settled
      FROM refund_mothers m LEFT JOIN refund_execution e ON e.mother_id=m.id LEFT JOIN refunds r ON r.id=e.refund_id
      WHERE m.deleted_at IS NULL GROUP BY m.id ORDER BY m.name,m.id""")
    for row in rows:
        check = row.pop('check_json') or {}
        capacity = row['management_json'].get('capacity') or {}
        if check.get('seats') and check.get('checked_at') and (not capacity.get('checked_at') or parsedate(check['checked_at']) > parsedate(capacity['checked_at'])):
            row['management_json']['capacity'] = {**check['seats'], 'checked_at': check['checked_at']}
    return {'items': rows}


@router.get('/{id}/credentials')
def credentials(id: str, request: Request):
    mother = mother_row(id)
    with db.transaction() as q:
        audit(q, request, 'refund.mother.credentials', id, '查看母号凭据')
    return {'email': mother['email'], 'team_id': mother['team_id'],
            'access_token': open_secret('mother:' + id, mother['secret']),
            'refresh_token': open_secret('mother-refresh:' + id, mother['refresh_secret']) if mother['refresh_secret'] else '',
            'expires_at': mother['token_expires_at']}


@router.patch('/{id}/resource')
async def update_resource(id: str, request: Request):
    value = await body(request)
    require(isinstance(value, dict) and value and set(value) <= {'enabled', 'proxy_id'}, '资源设置无效')
    require('enabled' not in value or type(value['enabled']) is bool, '启用状态无效')
    with db.transaction() as q:
        q.execute('SELECT pg_advisory_xact_lock($1)', LOCK)
        mother = mother_row(id, q)
        if 'proxy_id' in value:
            proxy = value['proxy_id']
            require(isinstance(proxy, str) and q.scalar("SELECT EXISTS(SELECT 1 FROM refund_proxies WHERE id=$1 AND deleted_at IS NULL AND enabled AND target IN ('both','mother'))", proxy), '请选择已启用的母号专属代理')
            if proxy != mother['proxy_id']:
                require(not q.scalar("SELECT EXISTS(SELECT 1 FROM refund_mother_leases WHERE team_id=$1 AND expires_at>now()) OR EXISTS(SELECT 1 FROM refund_execution WHERE mother_id=$2 AND lease_until>now())", mother['team_id'], id), '母号正在执行请求，请等待结束再更换代理', 409)
                q.execute('UPDATE refund_mothers SET proxy_id=$2,check_json=NULL,updated_at=now() WHERE id=$1', id, proxy)
        if 'enabled' in value:
            q.execute('UPDATE refund_mothers SET enabled=$2,updated_at=now() WHERE id=$1', id, value['enabled'])
        audit(q, request, 'refund.mother.resource', id, '更新母号接单状态或专属代理')
    return {'ok': True}


@router.post('/{id}/refresh')
def refresh(id: str, request: Request):
    with mother_client(id, force_refresh=True) as (mother, client):
        pass
    with db.transaction() as q:
        audit(q, request, 'refund.mother.refresh', id, '刷新母号凭据')
    return {'ok': True, 'expires_at': mother['token_expires_at'], 'last_refreshed_at': mother['last_refreshed_at']}


def read_snapshot(id, kind):
    with readonly_client(id) as (mother, client):
        result = client.capacity() if kind == 'capacity' else client.plan()
        if kind == 'plan':
            require(result['email'] == mother['email'].lower(), '上游账号身份不匹配', 502)
        db.execute('UPDATE refund_mothers SET management_json=management_json||$2::jsonb WHERE id=$1 AND secret=$3',
                   id, {kind: result}, mother['secret'])
        return result


@router.post('/{id}/capacity')
def capacity(id: str):
    return read_snapshot(id, 'capacity')


@router.post('/{id}/plan')
def plan(id: str):
    return read_snapshot(id, 'plan')


def protected(mother, row):
    return not row['id'] or not row['email'] or row['email'] == mother['email'].lower() or row['role'] not in ('standard-user', 'member', 'user')


@router.get('/{id}/members')
def members(id: str, q: str = '', page: int = 1, page_size: int = 10, sort_by: str = 'email', sort_dir: str = 'asc', seat_type: str = '', status: str = ''):
    require(seat_type in ('', 'default', 'prolite') and status in ('', 'active', 'inactive'), '筛选条件无效')
    with readonly_client(id) as (mother, client):
        rows = client.members()
    snapshot = {'count': sum(r['active'] and not protected(mother, r) for r in rows), 'checked_at': now().isoformat()}
    db.execute('UPDATE refund_mothers SET management_json=management_json||$2::jsonb WHERE id=$1 AND secret=$3 AND deleted_at IS NULL',
               id, {'members': snapshot}, mother['secret'])
    rows = [r for r in rows if (not seat_type or r['seat_type'] == seat_type) and (not status or r['active'] == (status == 'active'))]
    saved = {r['email'] for r in db.all('SELECT email FROM refund_mother_children')}
    reserved = {r['email'] for r in db.all("SELECT lower(email) email FROM refunds WHERE status IN ('queued','processing','verifying','attention') ")}
    for row in rows:
        blocked = protected(mother, row) or row['email'] in reserved
        row.update(can_kick=not blocked, can_leave=not blocked and row['email'] in saved)
    return page_rows(rows, q=q, page=page, page_size=page_size, sort_by=sort_by, sort_dir=sort_dir,
                     default='email', search=lambda r: r['email'] + ' ' + r['name'],
                     columns={k: (lambda r, k=k: r.get(k)) for k in ('email', 'name', 'role', 'seat_type', 'created_time', 'active')})


@router.post('/children')
async def save_child(request: Request):
    value = await body(request)
    require(isinstance(value, dict) and isinstance(value.get('session'), str), '请输入子号 Session 或 AT')
    raw = value['session'].strip()
    require(0 < len(raw) <= 131072, '凭据内容过长')
    try:
        data = json.loads(raw) if raw.startswith('{') else {'access_token': raw}
        token = data.get('accessToken') or data.get('access_token')
        if token:
            claims = identity(token)
        else:
            require(isinstance(data.get('password'), str) and 0 < len(data['password']) <= 1024, '请输入有效的 Session / AT 或登录密码')
            require(email_valid(value.get('email')) and isinstance(value.get('user_id'), str) and 0 < len(value['user_id']) <= 150, '请从临停成员列表导入登录资料')
            claims = {'email': value['email'].strip().lower(), 'user_id': value['user_id']}
        for field in ('password', 'totp_secret', 'refreshToken', 'refresh_token'):
            require(data.get(field) is None or isinstance(data[field], str) and len(data[field]) <= 4096, '登录资料格式无效')
    except (ValueError, TypeError, AttributeError):
        raise UpstreamError('子号凭据格式无效') from None
    require(claims['email'] and claims['user_id'], '子号凭据缺少邮箱或用户 ID')
    require(not value.get('email') or value['email'].strip().lower() == claims['email'], '子号邮箱与凭据不一致')
    with db.transaction() as q:
        q.execute('SELECT pg_advisory_xact_lock($1)', LOCK)
        require(not active_child(claims['email'], q), '该子号正在办理代退，不能更新管理凭据', 409)
        q.execute("""INSERT INTO refund_mother_children(email,user_id,credentials_enc) VALUES($1,$2,$3)
          ON CONFLICT(email) DO UPDATE SET user_id=excluded.user_id,credentials_enc=excluded.credentials_enc,updated_at=now()""",
                  claims['email'], claims['user_id'], encrypt(json.dumps({'access_token': token or '', 'refresh_token': data.get('refreshToken') or data.get('refresh_token') or '',
                            'password': data.get('password') or '', 'totp_secret': data.get('totp_secret') or ''})))
        audit(q, request, 'refund.mother.child.credentials', claims['email'], '更新成员管理凭据')
    return {'email': claims['email'], 'user_id': claims['user_id'], 'ok': True}


def login_child(email, value, proxy, heartbeat):
    require(value.get('password'), '子号 AT 失效且未保存登录资料，请重新导入 Session / AT')
    payload = json.dumps({'email': email, 'password': value['password'], 'totp_secret': value.get('totp_secret', ''), 'proxy': proxy})
    process = subprocess.Popen([sys.executable, str(db.ROOT / 'backend/refund_mother_login.py')], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    first = True
    started = time.monotonic()
    try:
        while time.monotonic() - started < 180:
            heartbeat()
            try:
                out, _ = process.communicate(payload if first else None, timeout=10)
                require(process.returncode == 0 and len(out) <= 131072, '子号登录未完成，请检查账号资料', 502)
                data = json.loads(out)
                require(data.get('ok') and data.get('access_token'), '子号登录未完成，请检查密码、验证方式或重新导入 Session', 502)
                return data
            except subprocess.TimeoutExpired:
                first = False
        require(False, '子号登录超时，请稍后核实', 502)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


@contextmanager
def child_client(email, user_id, team, heartbeat):
    row = db.one('SELECT * FROM refund_mother_children WHERE email=$1', email)
    require(row and row['user_id'] == user_id, '请先导入该子号的有效 Session / AT')
    value = json.loads(decrypt(row['credentials_enc']))
    cfg = db.one('SELECT child_proxy_id,child_proxy_backups FROM refund_settings WHERE id')
    candidates = [cfg['child_proxy_id'], *(cfg['child_proxy_backups'] or [])]
    proxies = [p for p in dict.fromkeys(candidates) if p and db.scalar("SELECT EXISTS(SELECT 1 FROM refund_proxies WHERE id=$1 AND enabled AND deleted_at IS NULL AND target IN ('both','child') AND (failure_until IS NULL OR failure_until<now()))", p)]
    require(proxies, '没有可用的子号代理')
    client = None
    def persist():
        new = encrypt(json.dumps(value))
        require(db.execute('UPDATE refund_mother_children SET credentials_enc=$2,updated_at=now() WHERE email=$1 AND credentials_enc=$3', email, new, row['credentials_enc']), '子号凭据已改变，请重新核实', 409)
        row['credentials_enc'] = new
    def save(updated):
        claims = identity(updated['access_token'])
        require(claims['email'] == email and claims['user_id'] == user_id and claims['expires'] > now().timestamp(), '新凭据的账号身份或有效期不匹配', 502)
        value.update(access_token=updated['access_token'], refresh_token=updated.get('refresh_token') or value.get('refresh_token', ''))
        value.pop('refresh_pending', None)
        persist()
        client.headers['Authorization'] = 'Bearer ' + value['access_token']
    def renew(url):
        require(not value.get('refresh_pending'), '上次子号凭据刷新结果不明，请重新导入有效凭据')
        if not value.get('refresh_token'):
            return login_child(email, value, url, heartbeat)
        value['refresh_pending'] = True
        persist()
        try:
            return client.refresh(value['refresh_token'])
        except UpstreamError as e:
            if e.http_status in (400, 401, 403, 429):
                value.pop('refresh_pending', None)
                if e.http_status in (400, 401) and value.get('password'):
                    value['refresh_token'] = ''
                    persist()
                    return login_child(email, value, url, heartbeat)
                persist()
            raise
    try:
        for index, proxy in enumerate(proxies):
            url = proxy_url(proxy, 'child')
            client = TeamClient(value.get('access_token', ''), team, url, heartbeat)
            claims = identity(value['access_token']) if value.get('access_token') else None
            if claims:
                require(claims['email'] == email and claims['user_id'] == user_id, '子号凭据身份不匹配')
            if not claims or claims['expires'] < now().timestamp() + 60:
                # Refresh/login are mutations: an unknown response is never replayed on another proxy.
                save(renew(url))
            try:
                me = client.request('GET', '/me')
            except UpstreamError as e:
                if e.http_status == 401:
                    save(renew(url))
                    me = client.request('GET', '/me')
                elif e.http_status in (0, 408, 502, 503, 504):
                    db.execute("UPDATE refund_proxies SET failure_until=now()+(SELECT child_proxy_cooldown_seconds FROM refund_settings WHERE id)*interval '1 second',failure_code='network' WHERE id=$1", proxy)
                    if index == len(proxies)-1:
                        raise
                    client.close()
                    continue
                else:
                    raise
            require(str(me.get('email') or '').lower() == email, '上游返回的子号身份不匹配', 502)
            break
        yield client
    finally:
        if client:
            client.close()


@router.post('/{id}/members/remove')
async def remove_member(id: str, request: Request):
    import asyncio
    value = await body(request)
    require(isinstance(value, dict) and value.get('method') in ('mother_kick', 'child_leave'), '移出方式无效')
    for field in ('email', 'user_id', 'team_id'):
        require(isinstance(value.get(field), str) and 0 < len(value[field]) <= 320, '成员资料不完整')
    def execute():
        with mother_client(id, child_email=value['email'].strip().lower()) as (mother, client):
            require(mother['team_id'] == value['team_id'], '母号团队已改变，请刷新列表', 409)
            email = value['email'].strip().lower()
            require(not active_child(email), '该子号正在办理代退，不能移出', 409)
            rows = client.members()
            row = next((r for r in rows if r['email'] == email or r['id'] == value['user_id']), None)
            if row:
                require(row['email'] == email and row['id'] == value['user_id'] and not protected(mother, row),
                        '成员身份已改变或不允许操作，请刷新列表', 409)
                try:
                    if value['method'] == 'child_leave':
                        with child_client(email, row['id'], mother['team_id'], client.heartbeat) as child:
                            child.request('DELETE', child.path('/users/' + quote_id(row['id'])))
                    else:
                        client.request('DELETE', client.path('/users/' + quote_id(row['id'])))
                except UpstreamError:
                    pass  # A lost DELETE response is resolved by a fresh full roster.
                require(not any(r['id'] == row['id'] or r['email'] == email for r in client.members()), '成员仍在团队中，请核实后重试', 409)
            with db.transaction() as q:
                audit(q, request, 'refund.mother.member.remove', id, '移出成员 ' + email + '（' + value['method'] + '）')
        return {'ok': True}
    return await asyncio.to_thread(execute)


def quote_id(value):
    from urllib.parse import quote
    return quote(value, safe='')


def refresh_due():
    if not settings()['auto_refresh']:
        return
    rows = db.all("""SELECT id FROM refund_mothers WHERE deleted_at IS NULL AND refresh_secret IS NOT NULL
      AND token_expires_at<now()+interval '5 minutes' AND (refresh_attempt_at IS NULL OR refresh_attempt_at<now()-interval '5 minutes')
      ORDER BY token_expires_at LIMIT 2""")
    for row in rows:
        try:
            with mother_client(row['id']):
                pass
        except Exception:
            # Credential/transport errors never disable a mother or change its proxy.
            db.execute('UPDATE refund_mothers SET refresh_attempt_at=now() WHERE id=$1', row['id'])
