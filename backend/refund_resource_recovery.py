"""Bounded resource maintenance, independent of order fees and login retries."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from contextlib import contextmanager
from fastapi import APIRouter, Depends, Request

from . import db, deployment
from .common import administrator, audit, now, uid, require, BusinessError
from .listing import pagination, order_by, result
from . import refund_mothers as mothers
from .refund_mother_upstream import TeamClient, open_secret

router = APIRouter(prefix='/api/admin/refund/resource-recovery', dependencies=[Depends(administrator)])


def settings(q=db):
    return q.scalar('SELECT resource_recovery FROM refund_settings WHERE id')


def active(team, q=db):
    return q.scalar('''SELECT EXISTS(SELECT 1 FROM refund_execution e JOIN refund_mothers m ON m.id=e.mother_id
      WHERE m.team_id=$1 AND NOT e.released AND e.mother_released_at IS NULL)''', team)


@contextmanager
def lease(mother_id, team):
    token = uid()
    with db.transaction() as q:
        q.execute('SELECT pg_advisory_xact_lock($1)', mothers.LOCK)
        m = q.one('SELECT * FROM refund_mothers WHERE id=$1 AND team_id=$2', mother_id, team)
        require(m and not active(team, q), '母号仍有在途订单', 409)
        require(q.one('''INSERT INTO refund_mother_leases(team_id,token,expires_at) VALUES($1,$2,now()+interval '90 seconds')
          ON CONFLICT(team_id) DO UPDATE SET token=excluded.token,expires_at=excluded.expires_at
          WHERE refund_mother_leases.expires_at<=now() RETURNING token''', team, token), '母号正在操作', 409)
    def heartbeat():
        require(db.execute("UPDATE refund_mother_leases SET expires_at=now()+interval '90 seconds' WHERE team_id=$1 AND token=$2 AND expires_at>now()", team, token), '资源租约已失效', 409)
        db.execute("UPDATE refund_member_leases SET expires_at=now()+interval '90 seconds' WHERE token=$1 AND expires_at>now()", token)
    try:
        if m['deleted_at'] is None and mothers.settings()['auto_refresh']:
            m = mothers.refresh_locked(m, heartbeat)
        p = db.one('SELECT secret,enabled FROM refund_proxies WHERE id=$1', m['proxy_id'])
        require(p and p['enabled'], '母号代理不可用', 409)
        yield m, token, heartbeat, open_secret('mother:' + m['id'], m['secret']), open_secret('proxy:' + m['proxy_id'], p['secret'])
    finally:
        db.execute('DELETE FROM refund_member_leases WHERE token=$1', token)
        db.execute('DELETE FROM refund_mother_leases WHERE team_id=$1 AND token=$2', team, token)


def snapshot(team, at, proxy, heartbeat):
    def read(kind):
        c = TeamClient(at, team, proxy, heartbeat)
        try:
            return c.capacity() if kind == 'capacity' else c.collection(kind)
        finally:
            c.close()
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(read, ('capacity', 'users', 'invites')))


def target(row):
    email = row.get('email') or row.get('email_address') or ''
    identity = row.get('user_id') or row.get('id') or row.get('chatgpt_user_id') or ''
    require(isinstance(email, str) and isinstance(identity, str), '上游成员身份不完整', 502)
    return email.lower(), identity


def matching(rows, email, user_id, kind):
    selected = []
    for row in rows:
        address, identity = target(row)
        # A verified user ID remains valid when the upstream hides/changes email.
        require(address or kind == 'users' and user_id and identity, '上游成员身份不完整，尚不能确认已移出', 502)
        if address == email or kind == 'users' and user_id and identity == user_id:
            selected.append(row)
    return selected


def reserve_child(email, token, user_id=''):
    with db.transaction() as q:
        q.execute('SELECT pg_advisory_xact_lock($1)', mothers.LOCK)
        busy = q.scalar('''SELECT EXISTS(SELECT 1 FROM refunds r JOIN refund_execution e ON e.refund_id=r.id
          WHERE (lower(r.email)=$1 OR ($2<>'' AND e.child_user_id=$2)) AND NOT e.released AND e.mother_released_at IS NULL)''', email, user_id)
        require(not busy, '子号已有在途订单，未执行移出', 409)
        require(q.one('''INSERT INTO refund_member_leases(email,token,expires_at) VALUES($1,$2,now()+interval '90 seconds')
          ON CONFLICT(email) DO UPDATE SET token=excluded.token,expires_at=excluded.expires_at
          WHERE refund_member_leases.expires_at<=now() OR refund_member_leases.token=$2 RETURNING token''', email, token), '子号正在管理操作', 409)


def remove_rows(m, client, token, members, invites, email, user_id=''):
    for kind, rows in (('users', members), ('invites', invites)):
        for row in matching(rows, email, user_id, kind):
            address, identity = target(row)
            require(identity and (not user_id or kind == 'invites' or identity == user_id), '成员身份发生变化，等待核实', 409)
            require(address != m['email'].lower() and row.get('role') in ('standard-user', 'member', 'user'), '成员身份或角色无法确认', 409)
            reserve_child(email, token, identity if kind == 'users' else '')
            if address and address != email:
                reserve_child(address, token, identity)
            # DELETE errors do not establish absence. The next run re-reads first.
            if kind == 'users':
                client.request('DELETE', client.path('/users/' + mothers.quote_id(identity)))
            else:
                client.request('DELETE', client.path('/invites'), {'email_address': address})


def claim():
    if deployment.maintenance_active():
        return None
    with db.transaction() as q:
        q.execute('SELECT pg_advisory_xact_lock($1)', mothers.LOCK)
        if deployment.maintenance_active():
            return None
        if q.scalar("SELECT EXISTS(SELECT 1 FROM refund_resource_tasks WHERE lease_until>now())"):
            return None
        t = q.one('''SELECT t.* FROM refund_resource_tasks t WHERE t.state NOT IN ('armed','done') AND t.next_at<=now()
          AND (t.lease_until IS NULL OR t.lease_until<=now())
          AND NOT EXISTS(SELECT 1 FROM refund_execution e JOIN refund_mothers m ON m.id=e.mother_id
            WHERE m.team_id=t.team_id AND NOT e.released AND e.mother_released_at IS NULL)
          AND NOT EXISTS(SELECT 1 FROM refund_mother_leases l WHERE l.team_id=t.team_id AND l.expires_at>now())
          ORDER BY t.next_at,t.id LIMIT 1 FOR UPDATE''')
        if t:
            t['lease_token'] = uid()
            q.execute("UPDATE refund_resource_tasks SET lease_token=$2,lease_until=now()+interval '90 seconds' WHERE id=$1", t['id'], t['lease_token'])
        return t


def save(t, state, message, delay, clean=False):
    with db.transaction() as q:
        require(q.execute('''UPDATE refund_resource_tasks SET state=$3,message=$4,next_at=now()+$5*interval '1 second',
          clean_checks=CASE WHEN $6 THEN clean_checks+1 ELSE 0 END,updated_at=now()
          WHERE id=$1 AND lease_token=$2 AND lease_until>now()''', t['id'], t['lease_token'], state, message, delay, clean), '清理任务租约已失效', 409)
        if message != t['message']:
            q.execute('INSERT INTO refund_events(refund_id,message,internal) VALUES($1,$2,true)', t['refund_id'], '资源维护：' + message)


def run(t):
    cfg = settings()
    try:
        with lease(t['mother_id'], t['team_id']) as (m, token, beat, at, proxy):
            def heartbeat():
                beat()
                require(db.execute("UPDATE refund_resource_tasks SET lease_until=now()+interval '90 seconds' WHERE id=$1 AND lease_token=$2 AND lease_until>now()", t['id'], t['lease_token']), '清理任务租约已失效', 409)
            cap, members, invites = snapshot(t['team_id'], at, proxy, heartbeat)
            present = bool(matching(members, t['email'], t['user_id'], 'users') or matching(invites, t['email'], '', 'invites'))
            if t['state'] == 'review' and present:
                save(t, 'review', '提交结果仍需核实，未自动移出或重新提交', cfg['slow_retry_seconds'])
                return
            if present:
                # A late join starts a fresh observation period; prior clean
                # checks cannot be reused after an actual member was found.
                t['clean_checks'] = 0
                t['watch_until'] = now() + timedelta(seconds=cfg['watch_seconds'])
                require(db.execute('''UPDATE refund_resource_tasks SET clean_checks=0,watch_until=$3
                  WHERE id=$1 AND lease_token=$2 AND lease_until>now()''', t['id'], t['lease_token'], t['watch_until']), '清理任务租约已失效', 409)
                save(t, 'cleaning', '发现残留成员或邀请，继续清理', cfg['retry_seconds'])
                client = TeamClient(at, t['team_id'], proxy, heartbeat)
                try:
                    remove_rows(m, client, token, members, invites, t['email'], t['user_id'])
                finally:
                    client.close()
                cap, members, invites = snapshot(t['team_id'], at, proxy, heartbeat)
                present = bool(matching(members, t['email'], t['user_id'], 'users') or matching(invites, t['email'], '', 'invites'))
            require(not present, '成员或邀请尚未清理，继续复查', 409)
            seat = 'premium' if m['seat_type'] == 'prolite' else 'standard'
            require(cap[seat]['remaining'] > 0, '账号已不在列表，实际席位尚未释放', 409)
            done = t['watch_until'] and now() >= t['watch_until'] and t['clean_checks'] >= 1 and not present
            pending_state = 'review' if t['state'] == 'review' else 'watching'
            save(t, 'done' if done else pending_state, '成员、邀请及席位已确认恢复' if done else '已不在团队，等待再次复查', cfg['retry_seconds'], True)
    except BusinessError as e:
        attempts = t['attempts'] + 1
        try:
            db.execute('UPDATE refund_resource_tasks SET attempts=attempts+1 WHERE id=$1 AND lease_token=$2', t['id'], t['lease_token'])
            save(t, 'review' if t['state'] == 'review' else 'cleaning', e.message, cfg['slow_retry_seconds'] if attempts >= cfg['fast_attempts'] else cfg['retry_seconds'])
        except BusinessError:
            pass
    except Exception:
        try:
            db.execute('UPDATE refund_resource_tasks SET attempts=attempts+1 WHERE id=$1 AND lease_token=$2', t['id'], t['lease_token'])
            save(t, 'review' if t['state'] == 'review' else 'cleaning', '资源检查未完成，等待重试', cfg['slow_retry_seconds'])
        except BusinessError:
            pass
    finally:
        db.execute("UPDATE refund_resource_tasks SET lease_token='',lease_until=NULL WHERE id=$1 AND lease_token=$2", t['id'], t['lease_token'])


def probe_health():
    cfg = settings()
    h = db.one('''SELECT h.*,m.id mother_id,m.id||':'||md5(m.secret)||':'||md5(p.secret) current_key
      FROM refund_resource_health h JOIN refund_mothers m ON m.team_id=h.scope JOIN refund_proxies p ON p.id=m.proxy_id
      WHERE h.blocked AND h.scope<>'login' AND h.next_at<=now() AND m.deleted_at IS NULL
      AND NOT EXISTS(SELECT 1 FROM refund_resource_tasks t WHERE t.team_id=h.scope AND t.state IN ('armed','cleaning','review'))
      ORDER BY h.next_at,m.id LIMIT 1''')
    if not h:
        return
    if h['attempt_key'] and h['attempt_key'] != h['current_key']:
        db.execute('DELETE FROM refund_resource_health WHERE scope=$1 AND updated_at=$2', h['scope'], h['updated_at'])
        return
    try:
        with lease(h['mother_id'], h['scope']) as (m, token, heartbeat, at, proxy):
            cap, members, invites = snapshot(h['scope'], at, proxy, heartbeat)
            seat = 'premium' if m['seat_type'] == 'prolite' else 'standard'
            if cap[seat]['remaining'] < 1:
                # Persist responsibility before any mutation. The regular task
                # runner performs and verifies cleanup, including lost replies.
                refund_id = db.scalar('''SELECT e.refund_id FROM refund_execution e JOIN refund_mothers m ON m.id=e.mother_id
                  WHERE m.team_id=$1 ORDER BY e.updated_at DESC LIMIT 1''', h['scope'])
                require(refund_id, '缺少对应申请，等待人工核实', 409)
                for kind, rows in (('users', members), ('invites', invites)):
                    for row in rows:
                        email, identity = target(row)
                        if not email or not identity or email == m['email'].lower() or row.get('role') not in ('standard-user', 'member', 'user'):
                            continue
                        reserve_child(email, token, identity if kind == 'users' else '')
                        db.execute('''INSERT INTO refund_resource_tasks(id,refund_id,mother_id,team_id,email,user_id,state,watch_until)
                          VALUES($1,$2,$3,$4,$5,$6,'cleaning',now()+$7*interval '1 second') ON CONFLICT(refund_id,team_id,email)
                          DO UPDATE SET state=CASE WHEN refund_resource_tasks.state='review' THEN 'review' ELSE 'cleaning' END,
                          clean_checks=0,next_at=now(),watch_until=excluded.watch_until,updated_at=now()''',
                          uid(), refund_id, m['id'], h['scope'], email, identity if kind == 'users' else '', cfg['watch_seconds'])
            require(cap[seat]['remaining'] > 0, '席位仍不可用', 409)
            # A roster success cannot establish recovered invitation permission.
            require(h['reason'] != 'mother_invite_auth', '邀请权限需要更新凭据后核实', 409)
            db.execute('DELETE FROM refund_resource_health WHERE scope=$1 AND updated_at=$2', h['scope'], h['updated_at'])
    except Exception:
        db.execute("UPDATE refund_resource_health SET next_at=now()+$2*interval '1 second' WHERE scope=$1 AND updated_at=$3", h['scope'], cfg['slow_retry_seconds'], h['updated_at'])


def tick():
    if deployment.maintenance_active():
        return
    t = claim()
    if t:
        run(t)
    else:
        probe_health()


@router.get('')
def overview(page: int=1, page_size: int=10, q: str='', sort_by: str='next_at', sort_dir: str='asc'):
    pagination(page, page_size)
    require(len(q) <= 200, '搜索内容不能超过 200 个字符')
    ordering = order_by(sort_by, sort_dir, {k: 't.'+k for k in ('email','state','attempts','next_at','updated_at')}, 'next_at', 't.id')
    condition = "t.state NOT IN ('armed','done') AND ($1='' OR strpos(lower(t.email||' '||m.name||' '||t.refund_id),lower($1))>0)"
    with db.transaction() as query:
        total = query.scalar('SELECT count(*) FROM refund_resource_tasks t JOIN refund_mothers m ON m.id=t.mother_id WHERE '+condition, q)
        items = query.all('''SELECT t.id,t.refund_id,t.mother_id,m.name mother_name,t.email,t.state,t.attempts,t.next_at,t.message,t.updated_at
          FROM refund_resource_tasks t JOIN refund_mothers m ON m.id=t.mother_id WHERE '''+condition+' ORDER BY '+ordering+' LIMIT $2 OFFSET $3',q,page_size,(page-1)*page_size)
        health = query.all('''SELECT h.scope,h.failures,h.blocked,h.next_at,h.reason,
          (SELECT name FROM refund_mothers m WHERE m.team_id=h.scope ORDER BY m.updated_at DESC LIMIT 1) mother_name
          FROM refund_resource_health h WHERE h.blocked ORDER BY h.next_at''')
    return result(items,total,page,page_size,health=health)


@router.post('/{id}/retry')
def retry(id: str, request: Request):
    with db.transaction() as q:
        t = q.one('SELECT id,lease_until,state FROM refund_resource_tasks WHERE id=$1 FOR UPDATE', id)
        require(t and t['state'] not in ('armed','done'), '没有待处理的资源任务', 409)
        require(not t['lease_until'] or t['lease_until'] <= now(), '正在检查，请稍后再试', 409)
        q.execute('UPDATE refund_resource_tasks SET next_at=now() WHERE id=$1', id)
        audit(q, request, 'refund.resource.retry', id, '安排资源检查，不改变订单或费用')
    return {'ok': True}


@router.post('/health/{scope}/retry')
def retry_health(scope: str, request: Request):
    with db.transaction() as q:
        require(q.execute('UPDATE refund_resource_health SET next_at=now() WHERE scope=$1 AND blocked', scope), '资源没有处于冷却状态', 409)
        audit(q, request, 'refund.resource.probe', scope, '安排资源检查，不改变启用状态')
    return {'ok': True}
