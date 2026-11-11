"""Subscription manual proxy selection and network-only circuit breaking.

All allocation/state changes use the existing 1886003 transaction lock. Payment
submission fences remain in subscription_retry; this module never sends requests.
"""
import re

from . import db
from .common import config, encrypt, now, uid
from .country_pool import PoolError
from .checkout_errors import CheckoutError


def network_failure(exc):
    from .payment_trace import error_code
    # Authentication, challenges, HTTP rejection and local pool contention are
    # not evidence of a broken route. Never persist the original exception text.
    text = str(exc).lower()
    if re.search(r'\b(?:401|403|407|429)\b|certificate|invalid.?password|authentication|challenge', text):
        return False
    if type(exc).__name__ == 'PoolTimeout':
        return False
    if isinstance(exc, CheckoutError):
        return exc.category in {'timeout', 'proxy_connect', 'dns', 'connection'}
    return error_code(exc) in {'timeout', 'proxy_connect', 'dns', 'connection'} or bool(
        re.fullmatch(r'(?:提链失败:\s*)?network_error', text.strip()))


def select(q, country, owner, seconds, excluded=()):
    """Caller holds the proxy lock. Capacity alone never activates a backup."""
    cfg = config('proxy', q)
    rows = q.all("SELECT r.*, (SELECT count(*) FROM proxy_leases l WHERE l.resource_id=r.id AND l.expires_at>now()) AS in_use "
                 "FROM resources r WHERE r.enabled AND r.kind='proxy' "
                 "AND (r.country=$1 OR (r.vendor='iproyal' AND r.ip_mode='dynamic')) "
                 "AND (r.cooldown_until IS NULL OR r.cooldown_until<=now()) "
                 "AND (r.proxy_failures=0 OR r.probe_until IS NULL OR r.probe_until<=now()) "
                 "ORDER BY r.failover_priority,(r.id=$2) DESC,(r.last_status='failed'),random()*r.weight DESC",
                 country, cfg.get('default_id', ''))
    # A retry prefers a route this order has not already failed on, even when
    # its delay exceeded the cooldown. Once all were tried, half-open rules apply.
    untried = [r for r in rows if r['id'] not in excluded]
    rows = untried or rows
    if not rows:
        raise PoolError('pool_capacity')
    priority = rows[0]['failover_priority']
    row = next((r for r in rows if r['failover_priority'] == priority and r['in_use'] < r['capacity']), None)
    if not row:
        raise PoolError('pool_capacity')
    lease = uid()
    q.execute("INSERT INTO proxy_leases(id,resource_id,expires_at) VALUES($1,$2,now()+$3*interval '1 second')",
              lease, row['id'], seconds)
    if row['proxy_failures']:
        q.execute("UPDATE resources SET probe_owner=$2,probe_until=now()+$3*interval '1 second' WHERE id=$1",
                  row['id'], owner, seconds)
    return row, lease


def acquire_order(q, order_id, country, seconds, build_endpoint):
    excluded = q.scalar('SELECT manual_proxy_failures FROM subscription_runs WHERE order_id=$1', order_id) or []
    row, lease = select(q, country, order_id, seconds, excluded)
    endpoint, lifetime = build_endpoint(row, country)
    q.execute("INSERT INTO order_proxy_sessions(order_id,resource_id,endpoint_enc,expires_at,proxy_lease_id,resource_revision,health_generation) "
              "VALUES($1,$2,$3,now()+$4*interval '1 second',$5,$6,$7)",
              order_id, row['id'], encrypt(endpoint), lifetime, lease, row['proxy_revision'], row['health_generation'])
    return endpoint


def failed(q, order_id):
    """Record a confirmed network failure; retain the binding until safe retry."""
    q.execute('SELECT pg_advisory_xact_lock(1886003)')
    binding = q.one("SELECT * FROM order_proxy_sessions WHERE order_id=$1 AND source_mode='manual'", order_id)
    if not binding:
        return False
    seconds = config('proxy', q).get('failure_cooldown_seconds', 300)
    q.execute("UPDATE resources SET proxy_failures=proxy_failures+1,health_generation=health_generation+1,"
              "cooldown_until=now()+$3*interval '1 second',probe_owner='',probe_until=NULL "
              "WHERE id=$1 AND proxy_revision=$2", binding['resource_id'], binding['resource_revision'], seconds)
    q.execute("UPDATE subscription_runs SET manual_proxy_failures=CASE WHEN $2=ANY(manual_proxy_failures) "
              "THEN manual_proxy_failures ELSE array_append(manual_proxy_failures,$2) END WHERE order_id=$1",
              order_id, binding['resource_id'])
    return True


def succeeded(order_id, q=None):
    if q is None:
        with db.transaction() as tx:
            return succeeded(order_id, tx)
    q.execute('SELECT pg_advisory_xact_lock(1886003)')
    # A success from an older in-flight order cannot erase a more recent failure.
    q.execute("UPDATE resources r SET proxy_failures=0,cooldown_until=NULL,probe_owner='',probe_until=NULL "
              "FROM order_proxy_sessions s WHERE s.order_id=$1 AND s.source_mode='manual' "
              "AND r.id=s.resource_id AND r.proxy_revision=s.resource_revision AND r.health_generation=s.health_generation",
              order_id)


def release(q, order_id):
    q.execute('SELECT pg_advisory_xact_lock(1886003)')
    q.execute("DELETE FROM proxy_leases l USING order_proxy_sessions s WHERE s.order_id=$1 "
              "AND s.source_mode='manual' AND l.id=s.proxy_lease_id", order_id)
    q.execute("UPDATE resources SET probe_owner='',probe_until=NULL WHERE probe_owner=$1", order_id)


def restart(q, order_id):
    """Only called under locked order/run rows before any payment submission."""
    release(q, order_id)
    removed = q.execute("DELETE FROM order_proxy_sessions WHERE order_id=$1 AND source_mode='manual'", order_id)
    if removed:
        q.execute("UPDATE subscription_runs SET checkout_checkpoint_enc='',checkout_url_enc='',checkout_session_id='' WHERE order_id=$1", order_id)
    return bool(removed)
