"""Customer guidance from committed login failures; no upstream calls or writes."""
from . import db
from .common import parsedate

PHONE_CODE = 'phone_verification_required'
PHONE_REASON = '上游要求手机验证，本次登录未完成。'
STATES = ('processing', 'verifying', 'attention', 'failed')


def messages(rows, q=None):
    # Callers supply only the current page after ownership/administrator checks.
    selected = {r['id']: r for r in rows if r.get('status') in STATES}
    if not selected:
        return {}
    facts = (q or db).all("""
      SELECT r.id,r.status,r.fee_status,r.fee,r.updated_at,d.reason
      FROM refunds r JOIN refund_execution x ON x.refund_id=r.id
      JOIN LATERAL (SELECT id,created_at FROM refund_events
        WHERE refund_id=r.id AND internal
          AND message='正在使用密码和 2FA 登录受邀团队。'
        ORDER BY id DESC LIMIT 1) l ON true
      JOIN LATERAL (SELECT diagnostic->'evidence'->>'reason' AS reason
        FROM refund_execution_diagnostics WHERE refund_id=r.id AND created_at>=l.created_at
          AND diagnostic->>'code'<>'protocol_trace'
          AND diagnostic->'evidence'->>'side'='child'
          AND diagnostic->'evidence'->>'stage'='login'
          AND diagnostic->'evidence'->>'event' IN ('failure','error')
        ORDER BY id DESC LIMIT 1) d ON true
      WHERE r.id=ANY($1) AND r.status=ANY($2) AND x.accepted_at IS NULL
        AND x.step IN ('login','failed')
        AND NOT EXISTS(SELECT 1 FROM refund_events e WHERE e.refund_id=r.id
          AND e.internal AND e.id>l.id
          AND e.message='已确认团队会话，准备提交导出并停用个人空间。')
    """, list(selected), list(STATES))
    result = {}
    for fact in facts:
        row = selected[fact['id']]
        # A job can advance between the authorized RPC and this query. Do not
        # attach a new attempt's explanation to an older response snapshot.
        if fact['status'] != row['status'] or ('fee_status' in row and fact['fee_status'] != row['fee_status']):
            continue
        if 'updated_at' in row:
            updated = parsedate(row['updated_at']) if isinstance(row['updated_at'], str) else row['updated_at']
            if updated != fact['updated_at']:
                continue
        if fact['reason'] != 'phone_verification_skipped':
            continue
        if fact['status'] == 'failed' and fact['fee_status'] == 'released':
            message = PHONE_REASON + ('本单无需服务费。' if fact['fee'] == 0 else '冻结服务费已释放。')
            message += '请先在官方登录页面完成手机验证，再重新提交。'
        else:
            message = PHONE_REASON + '本单尚待处理，请联系客服处理原订单，暂勿重复提交。'
        result[fact['id']] = message
    return result


def project(result):
    rows = result.get('items') if 'items' in result else [result['order']]
    guidance = messages(rows)
    for row in rows:
        if row['id'] in guidance:
            row['message'] = guidance[row['id']]
            row['error_code'] = PHONE_CODE
            row.pop('http_status', None)
    # Only replace a final public event, never a progress event or saved history.
    order = result.get('order', {})
    if order.get('id') in guidance and order.get('status') == 'failed':
        for event in reversed(result.get('events') or []):
            if event['message'] in (
                '本次服务未完成，冻结服务费已释放。',
                '本次服务未完成，冻结服务费已释放',
            ):
                event['message'] = guidance[order['id']]
                break
    return result
