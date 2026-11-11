"""Batch, read-only timing projections. Never use a mutable order updated_at."""
from . import db


def seconds(start, end):
    if start is None or end is None or end < start:
        return None
    return int((end-start).total_seconds())


def subscriptions(rows, q=None):
    if not rows:
        return
    values=(q or db).all("""SELECT o.id,o.created_at,now() AS measured_at,
      coalesce(e.started_at,t.started_at) AS started_at,
      coalesce(t.paid_at,s.paid_at) AS paid_at,
      coalesce(t.finished_at,e.finished_at) AS finished_at,
      (o.status IN ('queued','processing') OR o.status='succeeded' AND
       (o.activation='pending' OR o.renewal IN ('pending','retrying')) OR
       EXISTS(SELECT 1 FROM subscription_recoveries r WHERE r.order_id=o.id AND r.state IN ('armed','queued','running'))) AS ongoing
      FROM orders o LEFT JOIN subscription_runs s ON s.order_id=o.id
      LEFT JOIN LATERAL (SELECT min(created_at) FILTER(WHERE stage='processing') AS started_at,
        max(created_at) FILTER(WHERE stage IN ('failed','cancelled','admin_closed','post_payment_review','renewal_disabled','renewal_kept') OR o.workflow='link' AND stage='awaiting_payment') AS finished_at
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


def refunds(rows, q=None):
    if not rows:
        return
    values=(q or db).all("""SELECT r.id,r.created_at,now() AS measured_at,
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


def _total_timings(rows, values):
    timing={}
    for value in values:
        end=None if value['ongoing'] else value['finished_at']
        timing[value['id']]=dict(finished_at=end,ongoing=value['ongoing'],
            total_seconds=seconds(value['created_at'],value['measured_at'] if value['ongoing'] else end))
    for row in rows:
        row['timing']=timing.get(row['id'],{})


def activity(rows, q):
    """Only enrich the selected page, using its existing read-only snapshot."""
    subscriptions([r for r in rows if r['business']=='subscription' and r['workflow']!='renewal'],q)
    refunds([r for r in rows if r['business']=='refund'],q)
    registrations=[r for r in rows if r['business']=='registration']
    if registrations:
        values=q.all("""SELECT o.id,o.created_at,now() AS measured_at,
          o.status NOT IN ('completed','partial','failed','cancelled') AS ongoing,
          (SELECT max(created_at) FROM ledger WHERE registration_order_id=o.id
           AND business='registration' AND kind IN ('charge','unfreeze')) AS finished_at
          FROM platform_orders o WHERE o.id=ANY($1)""",[r['id'] for r in registrations])
        _total_timings(registrations,values)
    renewals=[r for r in rows if r['workflow']=='renewal']
    if renewals:
        values=q.all("""SELECT t.id,t.created_at,now() AS measured_at,
          t.status IN ('queued','processing','checking') AS ongoing,
          (SELECT max(created_at) FROM renewal_events WHERE task_id=t.id
           AND status IN ('disabled','no_subscription','review')) AS finished_at
          FROM renewal_tasks t WHERE t.id=ANY($1)""",[r['id'] for r in renewals])
        _total_timings(renewals,values)
