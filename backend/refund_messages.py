"""Customer guidance from committed login failures; no upstream calls or writes."""
from . import db
from .common import parsedate

PHONE_CODE = 'phone_verification_required'
PHONE_REASON = '上游要求手机验证，本次登录未完成。'
STATES = ('processing', 'verifying', 'attention', 'failed')


def recovery_messages(rows, q=None):
    selected = {r['id']: r for r in rows if r.get('status') in ('queued', 'attention')}
    if not selected:
        return {}
    facts = (q or db).all("""
      SELECT r.id,r.status,r.fee_status,r.updated_at,x.fallback_final,
        x.last_diagnostic->'evidence'->>'reason' AS reason,
        EXISTS(SELECT 1 FROM refunds old JOIN refund_execution e ON e.refund_id=old.id
          WHERE lower(old.email)=lower(r.email) AND old.id<>r.id
            AND NOT e.released AND old.status='attention') AS unresolved,
        EXISTS(SELECT 1 FROM refund_resource_tasks t WHERE t.email=lower(r.email) AND t.state='review') AS review,
        EXISTS(SELECT 1 FROM refund_resource_tasks t WHERE t.email=lower(r.email) AND t.state IN ('cleaning','watching')) AS cleaning
      FROM refunds r LEFT JOIN refund_execution x ON x.refund_id=r.id
      WHERE r.id=ANY($1) AND r.status IN ('queued','attention')
    """, list(selected))
    result = {}
    reasons = {'network_error':'网络连接失败。', 'email_verification_after_password':'登录仍需邮箱验证。',
               'phone_verification_skipped':'登录需要手机验证。', 'account_deactivated':'上游提示账号已停用。'}
    for fact in facts:
        row = selected[fact['id']]
        if fact['status'] != row['status'] or ('fee_status' in row and fact['fee_status'] != row['fee_status']):
            continue
        if 'updated_at' in row:
            updated = parsedate(row['updated_at']) if isinstance(row['updated_at'], str) else row['updated_at']
            if updated != fact['updated_at']:
                continue
        if fact['status'] == 'attention' and fact['fallback_final']:
            result[fact['id']] = ('个人套餐核验未完成。' + reasons.get(fact['reason'], '') +
                '处理结果尚未确认，服务费仍冻结、尚未扣除。请联系客服处理原订单，暂勿重复提交。')
        elif fact['status'] == 'queued':
            if fact['unresolved'] or fact['review']:
                result[fact['id']] = '该账号上一笔申请的结果尚待核实，请联系客服处理原订单。本单尚未执行，可取消释放冻结服务费。'
            elif fact['cleaning']:
                result[fact['id']] = '该账号上一笔申请正在清理团队占用，确认清理完成后继续本单；开始处理前可取消。'
    return result


def messages(rows, q=None):
    result = recovery_messages(rows, q)
    # Callers supply only the current page after ownership/administrator checks.
    selected = {r['id']: r for r in rows if r.get('status') in STATES}
    if not selected:
        return result
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
            if row['message'].startswith(PHONE_REASON):
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
