"""Admin-only, batched projections of persisted execution stages. No credentials."""
from collections import defaultdict
from . import db
from .payment_trace import PHASES

TERMINAL = {'completed':'已完成','succeeded':'已完成','partial':'部分交付，已结束',
            'failed':'已结束（失败）','banned':'已结束（账号不可用）','cancelled':'已取消'}
REFUND = {'assigned':'检查母号席位','inviting':'邀请加入团队','invited':'等待子号登录',
          'login':'登录子号','ready':'准备合并个人空间','submitting':'提交合并申请',
          'accepted':'合并已受理，等待移出','removing':'移出团队成员','completed':'已完成','failed':'已结束（失败）'}


def attach(rows, business=None, q=None):
    q = q or db
    groups = defaultdict(list)
    for row in rows:
        groups[business or row['business']].append(row)
    for kind, items in groups.items():
        ids = [r['id'] for r in items]
        if kind == 'subscription':
            traces = {r['order_id']:r for r in q.all("SELECT DISTINCT ON (order_id) order_id,phase,state,code FROM payment_trace_events "
                "WHERE order_id=ANY($1) AND phase NOT IN ('network','funds','complete') ORDER BY order_id,id DESC", ids)}
            recoveries = {r['order_id']:r['stage'] for r in q.all("SELECT order_id,stage FROM subscription_recoveries WHERE order_id=ANY($1) AND state IN ('armed','queued','running')", ids)}
            for r in items:
                status = r['status'];trace = traces.get(r['id'], {})
                if r.get('workflow') == 'renewal':
                    label = {'queued':'等待关闭续费','processing':'关闭自动续费','checking':'核实续费状态','disabled':'已关闭续费','no_subscription':'无有效订阅','review':'续费状态待核实','failed':'关闭续费失败'}.get(status, '等待处理')
                elif status in ('failed','cancelled'):
                    label = TERMINAL[status]
                elif status == 'draft':label = '等待提交'
                elif status == 'queued':label = '排队等待办理'
                elif status == 'awaiting_payment':label = '等待付款'
                elif r['id'] in recoveries:
                    label = {'payment':'核实付款结果','activation':'核实订阅生效','renewal':'处理自动续费'}.get(recoveries[r['id']], '核实办理结果')
                elif status == 'review':label = '付款结果待核实'
                elif status == 'succeeded' and r.get('workflow') == 'subscription':
                    if r.get('activation') not in ('active','not_checked'):label = '核实订阅生效'
                    elif r.get('renewal') in ('failed','review'):label = '取消续费待处理'
                    elif r.get('renewal') in ('pending','retrying','not_started'):label = '处理自动续费'
                    else:label = '已完成'
                elif status == 'succeeded':label = '已完成'
                else:
                    label = PHASES.get(trace.get('phase'), '准备办理')
                    if trace.get('state') == 'failed':label += '（待重试）'
                    elif trace.get('state') == 'review':label += '（待核实）'
                r['current_stage'] = label
        elif kind == 'refund':
            executions = {r['refund_id']:r for r in q.all('SELECT refund_id,step,recovery_mode,verification_only,released FROM refund_execution WHERE refund_id=ANY($1)', ids)}
            for r in items:
                e = executions.get(r['id'], {});status=r['status']
                if status in TERMINAL:label=TERMINAL[status]
                elif status == 'queued':label='排队等待母号'
                elif e.get('verification_only') or e.get('recovery_mode') in ('verify','fallback'):label='核实合并与退款结果'
                elif e.get('recovery_mode') == 'cleanup':label='清理团队成员与邀请'
                elif e.get('recovery_mode') == 'mother_switch':label='等待更换母号'
                else:
                    label=REFUND.get(e.get('step'), '等待处理')
                    if e.get('recovery_mode') == 'retry':label+='（等待重试）'
                    elif status == 'attention':label+='（待核实）'
                r['current_stage']=label
        elif kind == 'registration':
            counts=defaultdict(list)
            if q.scalar("SELECT to_regclass('platform_items')"):
                for item in q.all("SELECT order_id,status,stage,count(*) AS count FROM platform_items WHERE order_id=ANY($1) "
                    "AND status IN ('queued','running','oauth_pending','review') GROUP BY order_id,status,stage ORDER BY order_id,status,stage", ids):
                    label = {'queued':'排队','review':'结果核实','oauth_pending':'等待 OAuth 授权'}.get(item['status'], 'OAuth 授权' if item['stage']=='oauth' else '账号注册')
                    counts[item['order_id']].append(f"{label} {item['count']}")
            for r in items:
                r['current_stage'] = TERMINAL.get(r['status']) or ' · '.join(counts[r['id']]) or ('排队等待办理' if r['status']=='queued' else '等待处理')
    return rows
