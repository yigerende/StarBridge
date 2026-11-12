"""Administrator controls for target-account submission restrictions."""
from fastapi import APIRouter, Depends, Request
from . import db
from .common import administrator, audit, require
from .listing import pagination, order_by, result

router = APIRouter(prefix='/api/admin/refund/account-restrictions', dependencies=[Depends(administrator)])
REASONS = {
    'account_deactivated': '上游账号已停用',
    'account_deleted': '上游账号已删除',
    'account_banned': '上游账号已封禁',
    'account_disabled': '上游账号已禁用',
    'account_suspended': '上游账号已暂停',
}


@router.get('')
def listing(page: int=1, page_size: int=10, q: str='', status: str='', sort_by: str='first_seen_at', sort_dir: str='desc'):
    pagination(page, page_size)
    require(len(q) <= 200 and status in ('', 'blocked', 'allowed', 'cleared'), '筛选条件无效')
    ordering = order_by(sort_by, sort_dir,
                        {k: 'b.'+k for k in ('email', 'first_seen_at', 'last_seen_at', 'resubmissions', 'cleared_at')},
                        'first_seen_at', 'b.email')
    source = ''' FROM refund_account_restrictions b CROSS JOIN refund_settings s
      WHERE s.id AND ($1='' OR strpos(b.email,lower($1))>0)
      AND ($2='' OR ($2='cleared' AND b.cleared_at IS NOT NULL)
      OR ($2='blocked' AND b.cleared_at IS NULL AND b.resubmissions>=s.deactivated_resubmit_limit)
      OR ($2='allowed' AND b.cleared_at IS NULL AND b.resubmissions<s.deactivated_resubmit_limit))'''
    with db.transaction() as query:
        total = query.scalar('SELECT count(*)'+source, q.strip(), status)
        items = query.all('''SELECT b.email,b.reason,b.first_seen_at,b.last_seen_at,b.resubmissions,b.cleared_at,
          s.deactivated_resubmit_limit,
          CASE WHEN b.cleared_at IS NOT NULL THEN 'cleared'
            WHEN b.resubmissions>=s.deactivated_resubmit_limit THEN 'blocked' ELSE 'allowed' END status'''
          +source+' ORDER BY '+ordering+' LIMIT $3 OFFSET $4', q.strip(), status, page_size, (page-1)*page_size)
    for item in items:
        item['reason_label'] = REASONS.get(item['reason'], '上游账号不可用')
    return result(items, total, page, page_size)


@router.post('/{email}/clear')
def clear(email: str, request: Request):
    require(len(email) <= 320, '账号无效')
    with db.transaction() as q:
        # Serialize with new submissions; do not alter running orders or fees.
        q.execute('SELECT pg_advisory_xact_lock(718374024)')
        # Match the wallet/execution lock order before taking a restriction row.
        # The audit FK also touches this administrator when they own an order.
        q.execute('SELECT id FROM users WHERE id=$1 FOR UPDATE', request.state.user['id'])
        row = q.one('SELECT * FROM refund_account_restrictions WHERE email=$1 FOR UPDATE', email.strip().lower())
        require(row, '记录不存在', 404)
        if row['cleared_at'] is None:
            q.execute('''UPDATE refund_account_restrictions SET cleared_at=clock_timestamp(),cleared_by=$2,resubmissions=0
              WHERE email=$1''', row['email'], request.state.user['id'])
            audit(q, request, 'refund.account.clear', row['email'],
                  {'reason': row['reason'], 'first_seen_at': row['first_seen_at'].isoformat(), 'resubmissions': row['resubmissions']})
    return {'ok': True}
