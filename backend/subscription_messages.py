"""Read-only customer guidance from committed subscription facts; no upstream I/O."""
from . import db

REASONS = {
    'invalid_session': '当前 Session 已失效或无法刷新。',
    'session_expired': '当前 Session 已失效或无法刷新。',
    'session_token_required': '提交的 Session 缺少刷新所需的 sessionToken 或会话 Cookie。',
    'account_mismatch': '提交的 Session 与本单办理账号不一致。',
}


def messages(rows, q=None):
    """One bounded batch query, only for final failures or unresolved paid follow-ups."""
    ids = [r['id'] for r in rows if r.get('workflow') == 'subscription' and (
        r.get('status') in ('failed', 'review') or r.get('status') == 'succeeded' and
        (r.get('activation') == 'review' or r.get('renewal') == 'review'))]
    if not ids:
        return {}
    facts = (q or db).all("""
      WITH selected AS (
        SELECT o.id,o.user_id,o.status,o.fee,o.activation,o.renewal,s.paid_at,s.payment_submit_started_at
        FROM orders o JOIN subscription_runs s ON s.order_id=o.id
        WHERE o.id=ANY($1) AND o.workflow='subscription'
      ), funds AS (
        SELECT l.order_id,bool_or(l.kind='charge') AS charged,bool_or(l.kind='unfreeze') AS released,
          sum(coalesce(l.frozen_change,CASE WHEN l.kind='freeze' THEN o.fee
            WHEN l.kind IN ('unfreeze','charge') THEN -o.fee ELSE 0 END)) AS frozen
        FROM ledger l JOIN selected o ON l.order_id=o.id AND l.user_id=o.user_id
        WHERE l.business='subscription' GROUP BY l.order_id
      )
      SELECT o.id,o.status,o.fee,o.activation,o.renewal,o.paid_at,o.payment_submit_started_at,
        t.code,t.state AS terminal_state,d.original_code,f.warning_code,
        coalesce(funds.charged,false) AS charged,coalesce(funds.released,false) AS released,
        coalesce(funds.frozen,0) AS frozen
      FROM selected o LEFT JOIN funds ON funds.order_id=o.id
      LEFT JOIN subscription_followups f ON f.order_id=o.id
      LEFT JOIN LATERAL (SELECT id,code,state,round FROM payment_trace_events
        WHERE order_id=o.id AND phase='complete' ORDER BY id DESC LIMIT 1) t ON true
      LEFT JOIN LATERAL (SELECT diagnostics->>'response_error_code' AS original_code
        FROM payment_trace_events WHERE order_id=o.id AND id<=t.id AND phase<>'complete'
          AND state='failed' AND code=t.code AND round IS NOT DISTINCT FROM t.round
        ORDER BY id DESC LIMIT 1) d ON true
    """, ids)
    return {r['id']:message for r in facts if (message := guidance(r))}


def guidance(fact):
    paid = bool(fact.get('paid_at') or fact.get('charged') or fact['status'] == 'succeeded')
    if paid:
        # Resolved follow-ups must never inherit an earlier failed refresh.
        if fact.get('activation') != 'review' and fact.get('renewal') != 'review':
            return ''
        code = fact.get('warning_code')
        if code not in REASONS:
            return ''
        return '本单已付款成功。' + REASONS[code] + '后续核实尚未完成，请联系客服处理原订单，不要重新下单或付款。'
    if fact['status'] not in ('failed', 'review') or fact.get('terminal_state') not in ('failed', 'review'):
        return ''
    code = fact.get('code')
    if code not in REASONS:
        return ''
    if code == 'invalid_session' and fact.get('original_code') in REASONS:
        code = fact['original_code']
    reason = REASONS[code]
    if fact['status'] != 'failed' or fact.get('payment_submit_started_at'):
        return reason + '付款结果需要核实，请联系客服处理原订单，不要重新下单或付款。'
    released = fact['fee'] == 0 or fact.get('released') and fact.get('frozen') == 0
    if not released:
        return reason + '本单未提交付款，服务费状态待核实，请联系客服处理原订单，暂勿重新下单。'
    action = ('请重新获取本单办理账号的完整 Session 后重新提交。' if code == 'account_mismatch' else
              '请重新登录该账号，获取新的完整 Session 后重新提交。')
    return reason + action + '本单未提交付款，' + ('无需服务费。' if fact['fee'] == 0 else '冻结服务费已释放。')


def project_events(events, message, status):
    """Replace only the latest relevant event in the response, never stored history."""
    if message:
        stage = 'failed' if status == 'failed' else 'post_payment_review' if status == 'succeeded' else 'payment_review'
        for event in reversed(events):
            if event['stage'] == stage:
                event['message'] = message
                break
    return events
