"""Durable verification of the original payment and recovery of its paid tail.

This module has no checkout/create/confirm operation. Financial evidence is
required before settlement; queue exhaustion retains the held service fee.
"""
import json
import logging
from cryptography.exceptions import InvalidTag
from types import SimpleNamespace

from . import db, payment_trace as trace, subscription_payment_result as result
from .common import config, decrypt, encrypt, now
from .payment_transport import payment_session

log = logging.getLogger(__name__)
LOCK = 1886015
TRANSIENT = {'network_error', 'network_timeout', 'timeout', 'proxy_connect', 'connection',
             'http_408', 'http_429', 'http_500', 'http_502', 'http_503', 'http_504',
             'activation_pending', 'subscription_not_found', 'cancellation_pending',
             'cancellation_not_ready', 'followup_busy', 'deadline_exceeded'}


def policy(stage, q=db):
    payment, scheduler = config('payment', q), config('scheduler', q)
    if stage == 'payment':
        return payment['network_restart_limit'], scheduler['retry_delay_seconds']
    return payment['renewal_check_limit'], payment['renewal_check_interval_seconds']


def arm(q, order_id, context):
    timeout = config('payment', q)['subscription_wait_seconds']
    q.execute("INSERT INTO subscription_recoveries(order_id,context_enc,stage,state,deadline) "
              "SELECT s.order_id,$2,'payment','armed',LEAST(p.expires_at,s.credentials_expires_at,now()+$3*interval '1 second') "
              "FROM subscription_runs s JOIN order_proxy_sessions p USING(order_id) WHERE s.order_id=$1 "
              "ON CONFLICT(order_id) DO NOTHING", order_id, encrypt(json.dumps(context)), timeout)


def save_context(order_id, context):
    db.execute('UPDATE subscription_recoveries SET context_enc=$2,updated_at=now() WHERE order_id=$1',
               order_id, encrypt(json.dumps(context)))


def queue_payment(q, order_id):
    limit, delay = policy('payment', q)
    saved = q.one('SELECT state,stage,attempts,deadline FROM subscription_recoveries WHERE order_id=$1', order_id)
    if not saved:
        return False
    if saved['stage'] == 'payment' and saved['state'] in ('queued', 'running') and saved['deadline'] > now():
        return True
    if not limit or saved['deadline'] <= now():
        q.execute("UPDATE subscription_recoveries SET state='review',updated_at=now() WHERE order_id=$1", order_id)
        return False
    changed = q.execute("UPDATE subscription_recoveries SET state='queued',next_at=now()+$2*interval '1 second',updated_at=now() "
                        "WHERE order_id=$1 AND stage='payment' AND state='armed' AND deadline>now()", order_id, delay)
    if changed:
        q.execute("INSERT INTO order_events(order_id,stage,message) VALUES($1,'checking',"
                  "'付款响应尚未确认，系统将继续核实原交易；不会再次扣款，请勿重复提交。')", order_id)
        trace.record('checking', 'waiting', 'payment_verifying', order_id=order_id, q=q)
    return bool(changed)


def paid(q, order_id):
    timeout = config('payment', q)['subscription_wait_seconds']
    q.execute("UPDATE subscription_recoveries SET stage='followup',state='armed',attempts=0,"
              "deadline=LEAST((SELECT expires_at FROM order_proxy_sessions WHERE order_id=$1),now()+$2*interval '1 second'),"
              "next_at=now()+interval '5 seconds',updated_at=now() WHERE order_id=$1", order_id, timeout)


def close(q, order_id):
    q.execute("UPDATE subscription_recoveries SET state='completed',context_enc='',updated_at=now() WHERE order_id=$1", order_id)


def followup_saved(q, order_id, phase, warning):
    if phase == 'completed':
        close(q, order_id)
    elif warning:
        row = q.one('SELECT state,attempts,deadline FROM subscription_recoveries WHERE order_id=$1', order_id)
        if row:
            _reschedule(q, order_id, row, 'followup', warning)


def _reschedule(q, order_id, row, stage, code=''):
    limit, delay = policy(stage, q)
    retry = row['attempts'] < limit and row['deadline'] > now() and (stage == 'payment' or code in TRANSIENT)
    q.execute("UPDATE subscription_recoveries SET state=$2,next_at=now()+$3*interval '1 second',updated_at=now() WHERE order_id=$1",
              order_id, 'queued' if retry else 'review', delay)
    if stage == 'payment':
        if not retry:
            q.execute("UPDATE orders SET status='review',updated_at=now() WHERE id=$1 AND status='processing'", order_id)
            q.execute("INSERT INTO order_events(order_id,stage,message) VALUES($1,'payment_review',"
                      "'原交易持续核实仍未取得明确付款结果，已保留冻结资金，请人工核实；禁止重复付款。')", order_id)
            trace.record('result', 'review', 'payment_result_unconfirmed', order_id=order_id, q=q)
    elif retry:
        q.execute("UPDATE orders SET renewal=CASE WHEN renewal IN ('disabled','kept') THEN renewal ELSE 'retrying' END,"
                  "updated_at=now() WHERE id=$1 AND status='succeeded'", order_id)
        q.execute("INSERT INTO order_events(order_id,stage,message) VALUES($1,'renewal_pending',"
                  "'付款已成功，系统将恢复订阅核实和续费处理；不会再次付款。')", order_id)
    elif stage == 'followup':
        q.execute("UPDATE orders SET renewal=CASE WHEN renewal IN ('disabled','kept') THEN renewal ELSE 'review' END,"
                  "updated_at=now() WHERE id=$1 AND status='succeeded'", order_id)


def tick():
    from . import deployment
    if deployment.maintenance_active() or config('scheduler').get('paused') or config('payment').get('enabled') is not True:
        return
    # Terminal/deleted orders cannot occupy the bounded candidate window.
    db.execute("UPDATE subscription_recoveries r SET state='completed',context_enc='',updated_at=now() "
               "WHERE r.state IN ('armed','queued','running') AND EXISTS(SELECT 1 FROM orders o "
               "WHERE o.id=r.order_id AND (o.deleted_at IS NOT NULL OR o.status IN ('failed','cancelled')))")
    rows = db.all("SELECT order_id FROM subscription_recoveries WHERE state IN ('armed','queued','running') "
                  "AND next_at<=now() ORDER BY next_at LIMIT 4")
    for item in rows:
        with db.transaction() as guard:
            if not guard.scalar('SELECT pg_try_advisory_xact_lock($1,hashtext($2))', LOCK, item['order_id']):
                continue
            try:
                _run(item['order_id'])
            except Exception as exc:
                # Persist a bounded outcome rather than leaving a stuck running job.
                code = trace.error_code(exc)
                log.warning('Original transaction recovery interrupted: %s', code)
                with db.transaction() as q:
                    row = q.one("SELECT * FROM subscription_recoveries WHERE order_id=$1 AND state IN ('armed','running')", item['order_id'])
                    if row:
                        if code not in TRANSIENT:
                            row['attempts'] = 1000
                        _reschedule(q, item['order_id'], row, row['stage'], code)
        break


def _run(order_id):
    from . import subscription, subscription_cslive_go, go_post_payment, cslive_go_post_payment
    row = db.one("SELECT r.*,o.user_id,o.fee,o.status,o.deleted_at,s.paid_at,s.payment_submit_started_at,"
                 "s.subscription_flow,s.trace_round,s.checkout_session_id,p.endpoint_enc,p.expires_at FROM subscription_recoveries r "
                 "JOIN orders o ON o.id=r.order_id JOIN subscription_runs s USING(order_id) "
                 "LEFT JOIN order_proxy_sessions p USING(order_id) WHERE r.order_id=$1", order_id)
    if not row or row['state'] not in ('armed', 'queued', 'running') or row['next_at'] > now():
        return
    if row['deleted_at'] or row['status'] in ('failed', 'cancelled'):
        with db.transaction() as q: close(q, order_id)
        return
    if row['status'] == 'review':
        db.execute("UPDATE subscription_recoveries SET state='review',updated_at=now() WHERE order_id=$1", order_id)
        return
    limit, _ = policy(row['stage'])
    if row['attempts'] >= limit or row['deadline'] <= now() or not row['endpoint_enc'] or row['expires_at'] <= now():
        with db.transaction() as q: _reschedule(q, order_id, dict(row, attempts=1000), row['stage'])
        return
    if not row['payment_submit_started_at']:
        with db.transaction() as q: close(q, order_id)
        return  # never turn a pre-submit crash into a charge
    try:
        context = json.loads(decrypt(row['context_enc']))
    except (ValueError, TypeError, InvalidTag):
        with db.transaction() as q: _reschedule(q, order_id, dict(row, attempts=1000), row['stage'])
        return
    modules = {subscription.FLOW: (subscription, go_post_payment),
               subscription_cslive_go.FLOW: (subscription_cslive_go, cslive_go_post_payment)}
    if (not isinstance(context, dict) or row['subscription_flow'] not in modules
            or context.get('flow') != row['subscription_flow']
            or context.get('checkout_id') != row['checkout_session_id']):
        with db.transaction() as q: _reschedule(q, order_id, dict(row, attempts=1000), row['stage'])
        return
    module, followup = modules[row['subscription_flow']]
    db.execute("UPDATE subscription_recoveries SET state='running',attempts=attempts+1,"
               "next_at=now()+interval '5 seconds',updated_at=now() WHERE order_id=$1", order_id)
    row['attempts'] += 1
    endpoint = decrypt(row['endpoint_enc'])
    with trace.scope(order_id, row['trace_round']):
        if not row['paid_at']:
            with payment_session(endpoint) as session:
                session.headers.update({'Accept': 'application/json', 'Origin': 'https://pay.openai.com',
                                        'Referer': 'https://pay.openai.com/'})
                paid_result, decline, amount, _ = result.poll_result(SimpleNamespace(session=session),
                    context['checkout_id'], context['public_key'], context=context, attempts=1,
                    save=lambda value: save_context(order_id, value))
            if decline:
                module._fail_order(order_id, row['user_id'], row['fee'], '原交易已确认拒付，冻结服务费已释放。', 'payment_declined')
                return
            if not paid_result:
                with db.transaction() as q: _reschedule(q, order_id, row, 'payment')
                return
            if not module._succeed_order(order_id, row['user_id'], row['fee'], context['last4'],
                                         amount or context['amount'], context['currency']):
                return
        # Existing adapters persist a cancellation fence and check state before
        # another cancellation. Neither adapter contains a payment-submit path.
        outcome = followup.finish_payment(order_id, plan=context['plan'], proxy_url=endpoint,
                                          request_context=context.get('request_context'))
        if outcome and outcome.get('state') == 'completed':
            with db.transaction() as q: close(q, order_id)
        elif outcome and outcome.get('state') == 'busy':
            with db.transaction() as q:
                latest = q.one('SELECT * FROM subscription_recoveries WHERE order_id=$1', order_id)
                _reschedule(q, order_id, latest, 'followup', 'followup_busy')
