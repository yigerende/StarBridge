"""Subscription country inventory, with durable, exclusive per-task bindings.

The registration ProxyService manages an isolated catalog. Only the published
inventory is read here; no supplier request or session rewriting at allocation.
"""
from . import db
from .common import config, decrypt, now
from .country_pool import PoolError

LOCK = 1886018
MODE = 'national_pool'


def countries(q=db):
    q = q or db
    return {r['country'] for r in q.all('SELECT DISTINCT country FROM subscription_country_entries')}


def supports(country, q=db):
    codes = countries(q)
    return country in codes or 'GLOBAL' in codes


def reap(q=None):
    if q is None:
        with db.transaction() as tx:
            return reap(tx)
    return q.scalar('SELECT subscription_country_reap()')


def lock(q):
    # A provider refresh must not stall unrelated queues or hold a DB thread.
    if not q.scalar('SELECT pg_try_advisory_xact_lock($1)', LOCK):
        raise PoolError('pool_capacity')
    reap(q)


def reserve(q, entry_id, kind, owner, minutes=0):
    row = q.one('SELECT * FROM subscription_country_usage WHERE id=$1 FOR UPDATE', entry_id)
    if row and row['in_use']:
        if (row['owner_kind'], row['owner_id']) == (kind, owner):
            return
        raise PoolError('pool_capacity')
    q.execute("INSERT INTO subscription_country_usage(id,owner_kind,owner_id,session_minutes,in_use,last_used_at) "
              "VALUES($1,$2,$3,$4,true,now()) ON CONFLICT(id) DO UPDATE SET "
              "owner_kind=excluded.owner_kind,owner_id=excluded.owner_id,session_minutes=excluded.session_minutes,"
              "in_use=true,last_used_at=now(),released_at=NULL,cooldown_until=NULL", entry_id, kind, owner, minutes)


def pick(q, country, kind, owner):
    lock(q)
    # Same fallback as registration: prefer uncooled candidates; when all are
    # cooling use an unoccupied one. An occupied URL is never allocated twice,
    # including when it occurs in more than one country/source.
    row = q.one("SELECT e.* FROM subscription_country_entries e "
                "LEFT JOIN subscription_country_usage u ON u.id=e.id "
                "WHERE e.country IN ($1,'GLOBAL') AND NOT COALESCE(u.in_use,false) "
                "ORDER BY (e.country=$1) DESC,(COALESCE(u.cooldown_until,now())>now()),"
                "u.last_used_at ASC NULLS FIRST,random() LIMIT 1", country)
    if not row:
        raise PoolError('pool_capacity')
    reserve(q, row['id'], kind, owner, row['session_minutes'])
    return row


def acquire_order(q, order_id, country):
    row = pick(q, country, 'order', order_id)
    q.execute("INSERT INTO order_proxy_sessions(order_id,resource_id,endpoint_enc,expires_at,source_mode,session_ref,cooldown_minutes) "
              "VALUES($1,$2,$3,now()+interval '100 years','national_pool',$2,$4)",
              order_id, row['id'], row['endpoint_enc'], row['session_minutes'])
    return decrypt(row['endpoint_enc'])


def ensure_order(order_id, q=None):
    if q is None:
        with db.transaction() as tx:
            return ensure_order(order_id, tx)
    saved = q.one("SELECT resource_id,cooldown_minutes FROM order_proxy_sessions WHERE order_id=$1 AND source_mode='national_pool'", order_id)
    if saved:
        if q.scalar("SELECT EXISTS(SELECT 1 FROM subscription_country_usage WHERE id=$1 AND in_use AND owner_kind='order' AND owner_id=$2)", saved['resource_id'], order_id):
            return
        lock(q)
        reserve(q, saved['resource_id'], 'order', order_id, saved['cooldown_minutes'])


def acquire_renewal(q, task_id):
    saved = q.one('SELECT * FROM renewal_proxy_sessions WHERE task_id=$1 FOR UPDATE', task_id)
    if saved:
        if saved['source_mode'] != MODE:
            raise PoolError('proxy_mode_changed')
        if q.scalar("SELECT EXISTS(SELECT 1 FROM subscription_country_usage WHERE id=$1 AND in_use AND owner_kind='renewal' AND owner_id=$2)", saved['session_ref'], task_id):
            return decrypt(saved['endpoint_enc'])
        lock(q)
        reserve(q, saved['session_ref'], 'renewal', task_id, saved['cooldown_minutes'])
        return decrypt(saved['endpoint_enc'])
    country = config('national_pool', q).get('renewal_country', 'PH')
    row = pick(q, country, 'renewal', task_id)
    q.execute("INSERT INTO renewal_proxy_sessions(task_id,endpoint_enc,expires_at,session_ref,country,source_mode,cooldown_minutes) "
              "VALUES($1,$2,now()+interval '100 years',$3,$4,'national_pool',$5)",
              task_id, row['endpoint_enc'], row['id'], country, row['session_minutes'])
    return decrypt(row['endpoint_enc'])
