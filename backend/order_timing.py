"""Batch, read-only timing projections. Never use a mutable order updated_at."""
from . import db


def seconds(start, end):
    if start is None or end is None or end < start:
        return None
    return int((end-start).total_seconds())


def subscriptions(rows):
    if not rows:
        return
    values=db.all("""SELECT o.id,o.created_at,now() AS measured_at,
      coalesce(e.started_at,t.started_at) AS started_at,
      coalesce(t.paid_at,s.paid_at) AS paid_at,
      coalesce(t.finished_at,e.finished_at) AS finished_at,
      (o.status IN ('queued','processing') OR o.status='succeeded' AND
       (o.activation='pending' OR o.renewal IN ('pending','retrying')) OR
       EXISTS(SELECT 1 FROM subscription_recoveries r WHERE r.order_id=o.id AND r.state IN ('armed','queued','running'))) AS ongoing
      FROM orders o LEFT JOIN subscription_runs s ON s.order_id=o.id
      LEFT JOIN LATERAL (SELECT min(created_at) FILTER(WHERE stage='processing') AS started_at,
        max(created_at) FILTER(WHERE stage IN ('failed','cancelled','admin_closed','post_payment_review','renewal_disabled','renewal_kept')) AS finished_at
        FROM order_events WHERE order_id=o.id) e ON true
      LEFT JOIN LATERAL (SELECT min(created_at) FILTER(WHERE phase='queue' AND state='completed') AS started_at,
        min(created_at) FILTER(WHERE phase='result' AND state='completed' AND code='payment_succeeded') AS paid_at,
        max(created_at) FILTER(WHERE phase='complete') AS finished_at
        FROM payment_trace_events WHERE order_id=o.id) t ON true
      WHERE o.id=ANY($1)""",[r['id'] for r in rows])
    timing={}
    for v in values:
        end=None if v['ongoing'] else v['finished_at']
        timing[v['id']]=dict(started_at=v['started_at'],paid_at=v['paid_at'],finished_at=end,
            payment_seconds=seconds(v['started_at'],v['paid_at']),
            total_seconds=seconds(v['created_at'],v['measured_at'] if v['ongoing'] else end),ongoing=v['ongoing'])
    for row in rows:
        row['timing']=timing.get(row['id'],{})


def refunds(rows):
    if not rows:
        return
    values=db.all("""SELECT r.id,r.created_at,now() AS measured_at,
      r.status NOT IN ('succeeded','failed','banned','cancelled') AS ongoing,
      (SELECT max(l.created_at) FROM ledger l WHERE l.refund_order_id=r.id
       AND l.kind IN ('charge','unfreeze') AND l.business='refund') AS finished_at
      FROM refunds r WHERE r.id=ANY($1)""",[r['id'] for r in rows])
    timing={}
    for v in values:
        end=None if v['ongoing'] else v['finished_at']
        timing[v['id']]=dict(finished_at=end,
            total_seconds=seconds(v['created_at'],v['measured_at'] if v['ongoing'] else end),ongoing=v['ongoing'])
    for row in rows:
        row['timing']=timing.get(row['id'],{})
