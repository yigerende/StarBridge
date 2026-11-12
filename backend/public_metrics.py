"""Public order totals only. No identities, credentials, money or outcome breakdowns."""
import threading
import time
from datetime import datetime, timezone
from fastapi import APIRouter
from . import db
from .common import BusinessError

router = APIRouter(prefix='/api/public')
_lock = threading.Lock()
_cached = None
_expires = 0.0


def counts():
    global _cached, _expires
    # One aggregate refresh per process/minute, even during anonymous traffic bursts.
    with _lock:
        if time.monotonic() < _expires:
            if _cached is None:
                raise BusinessError('订单统计暂不可用', 503)
            return dict(_cached)
        try:
            with db.transaction() as q:
                q.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                q.execute("SET LOCAL statement_timeout='5s'")
                registered = bool(q.scalar("SELECT to_regclass('platform_orders')"))
                result = q.one("SELECT (SELECT count(*) FROM orders WHERE workflow='subscription') AS subscription,"
                               "(SELECT count(*) FROM refunds) AS refund,"+
                               ("(SELECT count(*) FROM platform_orders)" if registered else '0')+' AS registration')
            result['total'] = sum(result.values())
            result['updated_at'] = datetime.now(timezone.utc).isoformat()
            _cached, _expires = result, time.monotonic()+60
            return dict(result)
        except Exception:
            # Short failure backoff prevents a failing DB from being flooded.
            _cached, _expires = None, time.monotonic()+5
            raise BusinessError('订单统计暂不可用', 503) from None


@router.get('/order-counts')
def order_counts():
    # Count accepted business orders (not quantities), including administrators,
    # all outcomes and soft-deleted history. Standalone links/renewals are excluded.
    return counts()
