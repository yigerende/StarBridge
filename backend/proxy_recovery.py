"""Independent, lease-fenced connection checks for cooled subscription proxies."""
from . import db, deployment
from .common import config, uid
from .country_pool import mode


def claim():
    if deployment.maintenance_active():
        return None
    with db.transaction() as q:
        q.execute('SELECT pg_advisory_xact_lock(1886003)')
        if mode(q) != 'manual':
            return None
        cfg=config('proxy', q)
        required = cfg.get('recovery_success_count', 3)
        if required is None:
            q.execute("UPDATE resources SET proxy_failures=0,cooldown_until=NULL,probe_owner='',probe_until=NULL,"
                      "recovery_successes=0 WHERE enabled AND proxy_failures>0 AND cooldown_until<=now()")
            return None
        # A resource owns at most one live connection check, across app processes.
        row = q.one("SELECT r.* FROM resources r WHERE r.enabled AND r.kind='proxy' AND r.proxy_failures>0 "
                    "AND r.cooldown_until<=now() AND (r.probe_until IS NULL OR r.probe_until<=now()) "
                    "AND (r.recovery_checked_at IS NULL OR r.recovery_checked_at<now()-$1*interval '1 second') "
                    "AND NOT EXISTS(SELECT 1 FROM proxy_leases l WHERE l.resource_id=r.id AND l.expires_at>now()) "
                    "ORDER BY r.recovery_checked_at NULLS FIRST,r.id LIMIT 1 FOR UPDATE OF r", cfg.get('recovery_interval_seconds',5))
        if not row:
            return None
        token = uid()
        q.execute("INSERT INTO proxy_leases(id,resource_id,expires_at) VALUES($1,$2,now()+interval '60 seconds')", token, row['id'])
        q.execute("UPDATE resources SET probe_owner=$2,probe_until=now()+interval '60 seconds',recovery_checked_at=now() WHERE id=$1", row['id'], token)
        return dict(row, token=token, required=required)


def finish(job, passed):
    with db.transaction() as q:
        q.execute('SELECT pg_advisory_xact_lock(1886003)')
        cfg = config('proxy', q)
        q.execute('DELETE FROM proxy_leases WHERE id=$1', job['token'])
        # A newer failure, edit, disable, mode or threshold change invalidates it.
        if mode(q) != 'manual' or cfg.get('recovery_success_count', 3) != job['required']:
            q.execute("UPDATE resources SET probe_owner='',probe_until=NULL,recovery_successes=0 WHERE id=$1 AND probe_owner=$2", job['id'], job['token'])
            return
        row = q.one("SELECT recovery_successes FROM resources WHERE id=$1 AND enabled AND proxy_failures>0 "
                    "AND proxy_revision=$2 AND health_generation=$3 AND probe_owner=$4 AND probe_until>now() FOR UPDATE",
                    job['id'], job['proxy_revision'], job['health_generation'], job['token'])
        if not row:
            return
        successes = row['recovery_successes']+1 if passed else 0
        restored = successes >= job['required']
        q.execute("UPDATE resources SET recovery_successes=$2,recovery_checked_at=now(),probe_owner='',probe_until=NULL,"
                  "proxy_failures=CASE WHEN $3 THEN 0 ELSE proxy_failures END,"
                  "cooldown_until=CASE WHEN $3 THEN NULL WHEN NOT $4 THEN now()+$5*interval '1 second' ELSE cooldown_until END WHERE id=$1",
                  job['id'], successes, restored, passed, cfg.get('failure_cooldown_seconds', 300))


def tick():
    job = claim()
    if not job:
        return False
    passed = False
    try:
        from .manual_proxy_sessions import endpoint_for_order
        from .proxies import proxy_client, probe_exit
        endpoint, _ = endpoint_for_order(job, job['country'])
        # New client/session for each sample; no account, checkout or payment call.
        with proxy_client(endpoint, 10) as client:
            passed = probe_exit(client)['ok'] is True
    except Exception:
        pass  # Credentials and raw proxy exceptions must never reach logs.
    finally:
        finish(job, passed)
    return True
