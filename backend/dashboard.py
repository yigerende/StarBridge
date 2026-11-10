"""Read-only unified dashboards, using the shared database and its clock."""
from datetime import date, datetime, time, timedelta
from concurrent.futures import Future
from threading import Lock
from urllib.parse import urlencode

from . import db
from .common import config, require
from .features import capabilities
from .report_period import ZONE

_inflight = {}
_inflight_lock = Lock()


def shared_overview(user, **options):
    """Join concurrent identical reads; no result is cached after completion.

    Authentication runs before this function. User and role are part of the key,
    so simultaneous requests can never reuse another customer's snapshot.
    """
    key = (user['id'], user['role'], tuple(sorted(options.items())))
    with _inflight_lock:
        future = _inflight.get(key)
        leader = future is None
        if leader:
            future = Future()
            _inflight[key] = future
    if not leader:
        return future.result(timeout=30)
    try:
        result = overview(user, **options)
        future.set_result(result)
        return result
    except BaseException as error:
        future.set_exception(error)
        raise
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)


def order_source(registration, subscription=True):
    sources = []
    if subscription:
        sources.append("""SELECT o.id,o.user_id,u.email AS owner,'subscription'::text AS business,
          o.product_name AS title,o.email,o.status,o.workflow,o.activation,o.renewal,
          1 AS quantity,0 AS delivered,o.fee,o.created_at,o.updated_at,
          (o.status='succeeded' AND (o.workflow<>'subscription' OR
            (o.activation NOT IN ('pending','review') AND o.renewal NOT IN ('pending','retrying','review')))
            OR o.workflow='link' AND o.status='awaiting_payment') AS completed,
          (o.status='review' OR o.status='succeeded' AND o.workflow='subscription'
            AND (o.activation='review' OR o.renewal='review')) AS attention
          FROM orders o JOIN users u ON u.id=o.user_id WHERE o.deleted_at IS NULL""")
    if registration:
        sources.append("""SELECT o.id,u.id AS user_id,u.email AS owner,'registration'::text AS business,
          CASE WHEN o.need_oauth THEN '注册账号 · 含 OAuth' ELSE '注册账号 · 标准' END AS title,
          ''::text AS email,o.status,'registration'::text AS workflow,''::text AS activation,''::text AS renewal,
          o.quantity,o.delivered,(o.unit_price*o.quantity*100)::bigint AS fee,o.created_at,o.updated_at,
          o.status='completed' AS completed,o.status='review' AS attention
          FROM platform_orders o JOIN platform_users p ON p.id=o.user_id JOIN users u ON u.id=p.xufu_user_id""")
    # A typed empty relation also covers feature-restricted customer dashboards.
    if not sources:
        return order_source(False) + ' AND false'
    return ' UNION ALL '.join(sources)


def destination(path, business=None, **filters):
    params = dict(business=business) if business else {}
    params.update({k: v for k, v in filters.items() if v is not None})
    return '#' + path + ('?' + urlencode(params) if params else '')


def period(start, end, days):
    today = datetime.now(ZONE).date()
    require(days in (1, 7, 30), '请选择今天、近 7 天或近 30 天')
    try:
        first = date.fromisoformat(start) if start else today - timedelta(days=days-1)
        last = date.fromisoformat(end) if end else today
        require(1900 <= first.year <= last.year <= 9998 and 0 <= (last-first).days <= 365,
                '统计范围须为 1 至 366 天，结束日期不能早于开始日期')
    except (ValueError, TypeError):
        require(False, '统计日期格式无效')
    return first, last, datetime.combine(first, time(), ZONE), datetime.combine(last+timedelta(days=1), time(), ZONE)


def overview(user, *, admin=False, start='', end='', days=7, include_admin=False, focus='all'):
    require(focus in ('all', 'attention'), '订单筛选无效')
    first, last, begin, until = period(start, end, days)
    flags = capabilities(user)
    with db.transaction() as q:
        q.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        installed = bool(q.scalar("SELECT to_regclass('registration_reservations')"))
        reg = q.scalar('SELECT value FROM platform_settings WHERE id=1', default={}) if installed else {}
        reg = reg or {}
        reg_enabled = lambda key: installed and (user['role']=='admin' or reg.get('features', {}).get(key, True) is not False)
        reg_orders = reg_enabled('orders')
        source = order_source(installed if admin else reg_orders, admin or flags['orders'])
        scope = "(u.role<>'admin' OR $2::bool)" if admin else 'u.id=$1'
        args = (user['id'], include_admin, begin, until)
        cte = 'WITH activity AS (' + source + '), scoped AS (SELECT a.* FROM activity a JOIN users u ON u.id=a.user_id WHERE '+scope+') '
        queue_sql = """SELECT business,count(*) FILTER(WHERE status='queued') AS queued,
          count(*) FILTER(WHERE status IN ('processing','running')) AS running,
          count(*) FILTER(WHERE status='review') AS review,
          count(*) FILTER(WHERE status='succeeded' AND workflow='subscription' AND (activation='review' OR renewal='review')) AS followup,
          count(*) FILTER(WHERE status='succeeded' AND workflow='subscription' AND (activation='pending' OR renewal IN ('pending','retrying'))) AS settling
          FROM scoped GROUP BY business"""
        recent_sql = "SELECT * FROM scoped WHERE (NOT $5::bool OR attention) ORDER BY created_at DESC,business,id DESC LIMIT 8"
        projection = "SELECT (SELECT coalesce(jsonb_agg(r),'[]') FROM ("+queue_sql+") r) AS queues," + \
                     "(SELECT coalesce(jsonb_agg(r),'[]') FROM ("+recent_sql+") r) AS recent_orders"
        if admin:
            projection += """,(SELECT jsonb_build_object(
              'orders',count(*) FILTER(WHERE created_at >= $3 AND created_at < $4),
              'completed',count(*) FILTER(WHERE completed AND created_at >= $3 AND created_at < $4),
              'partial',count(*) FILTER(WHERE business='registration' AND status='partial' AND created_at >= $3 AND created_at < $4)) FROM scoped) AS totals,
              (SELECT coalesce(jsonb_agg(r),'[]') FROM (SELECT to_char(created_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD') AS day,
              business,count(*) AS value FROM scoped WHERE created_at >= $3 AND created_at < $4 GROUP BY 1,2) r) AS trend_orders"""
        activity = q.one(cte+projection, *args, focus=='attention')
        current, recent = activity['queues'], activity['recent_orders']
        queues = {r['business']: r for r in current}
        empty = dict(queued=0, running=0, review=0, followup=0, settling=0)
        for name in ('subscription', 'registration'):
            queues.setdefault(name, dict(empty, business=name))
        available = dict(available=0, expiring=0, next_order_id=None)
        if not admin and reg_enabled('accounts'):
            account_scope = '(u.role<>\'admin\' OR $2::bool)' if admin else 'u.id=$1'
            account_from = " FROM platform_accounts a JOIN platform_users p ON p.id=a.user_id JOIN users u ON u.id=p.xufu_user_id WHERE "+account_scope+" AND a.created_at>now()-interval '72 hours'"
            available = q.one("SELECT count(*) AS available,count(*) FILTER(WHERE a.created_at<=now()-interval '48 hours') AS expiring"+account_from, *args)
            available['next_order_id'] = q.scalar('SELECT a.order_id'+account_from+' ORDER BY a.created_at LIMIT 1', *args) if available['expiring'] else None
            ids = [r['id'] for r in recent if r['business']=='registration']
            account_counts = {r['order_id']:r['n'] for r in q.all('SELECT a.order_id,count(*) AS n'+account_from+' AND a.order_id=ANY($5) GROUP BY a.order_id', *args, ids)} if ids else {}
        else:
            account_counts = {}
        for row in recent:
            row['available_accounts'] = account_counts.get(row['id'], 0)
            row['href'] = destination('/admin/orders' if admin else '/orders', row['business'], order_id=row['id'])
            row['claim_href'] = destination('/accounts', 'registration', order_id=row['id']) if not admin and row['available_accounts'] else None
            if not admin:
                row.pop('owner', None)
                row.pop('user_id', None)
                if not flags['wallet']: row.pop('fee', None)
        attention = []
        def todo(key, title, count, href, hint, tone='warning', unit='笔'):
            attention.append(dict(key=key, title=title, count=count, href=href, hint=hint, tone=tone, unit=unit))
        sub, registration = queues['subscription'], queues['registration']
        order_path = '/admin/orders' if admin else '/orders'
        todo('payment_review','付款结果待核实',sub['review'],destination(order_path,'subscription',status='review'),
             '付款结果尚未确定，保留冻结；请勿再次付款。','danger')
        todo('followup','已付款，后续待处理',sub['followup'],destination(order_path,'subscription',attention='followup'),
             '付款已成功，仅处理订阅生效或续费设置。')
        todo('registration_review','注册状态待核实',registration['review'],destination(order_path,'registration',status='review'),
             '查看恢复进度与异常原因。')
        if admin:
            topups = q.scalar("SELECT count(*) FROM topups t JOIN users u ON u.id=t.user_id WHERE t.status='pending' AND "+scope, *args)
            # Security events are global, including anonymous traffic, independent of business/date/account scope.
            risks = q.scalar('SELECT count(*) FROM risk_events WHERE NOT resolved')
            todo('topups','充值待审核',topups,destination('/admin/topups',status='pending'),'核实到账凭据后入账。')
            todo('risks','风险事件待核查',risks,destination('/admin/risk',state='pending',all_time='true'),'全站未核查事件，包含未登录访问。')
            totals = activity['totals']
            # Unified ledger amounts use cents. Only actual charges/refunds enter net settlement.
            ledger_where = " FROM ledger l JOIN users u ON u.id=l.user_id WHERE "+scope+" AND l.kind IN ('charge','refund') AND l.created_at >= $3 AND l.created_at < $4"
            totals['attention'] = sum(t['count'] for t in attention)
            trend_orders = activity['trend_orders']
            trend_fees = q.all("SELECT to_char(l.created_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD') AS day,l.business,coalesce(sum(-l.amount),0) AS value"+ledger_where+' GROUP BY 1,2', *args)
            totals['settled'] = sum(row['value'] for row in trend_fees)
            series = {(first+timedelta(days=i)).isoformat(): dict(day=(first+timedelta(days=i)).isoformat(),subscription=0,registration=0,subscription_fees=0,registration_fees=0) for i in range((last-first).days+1)}
            for records, suffix in ((trend_orders,''),(trend_fees,'_fees')):
                for row in records:
                    if row['business'] in ('subscription','registration'): series[row['day']][row['business']+suffix] = row['value']
            # A delivered account is counted by its delivery timestamp, not the order creation date.
            delivered = q.scalar("SELECT count(*) FROM platform_accounts a JOIN platform_users p ON p.id=a.user_id JOIN users u ON u.id=p.xufu_user_id WHERE "+scope+' AND a.created_at >= $3 AND a.created_at < $4', *args) if installed else 0
            payment, scheduler = config('payment', q), config('scheduler', q)
            sub.update(accepting=payment.get('enabled') is True and payment.get('subscription_flow') in ('gener_direct_pay','cslive_go_direct_pay','cslive_full_go'),paused=scheduler.get('paused',False) or payment.get('enabled') is not True)
            registration.update(accepting=installed and reg.get('orders_open') is True,paused=not reg.get('worker_enabled',False),installed=installed,delivered=delivered)
            result = dict(totals=totals,trend=list(series.values()),start=first.isoformat(),end=last.isoformat(),include_admin=include_admin)
        else:
            if flags['orders']:
                todo('subscription_pending','订阅办理中',sub['queued']+sub['running']+sub['settling'],destination('/orders','subscription',attention='pending'),'排队、执行及付款后的自动核实。','info')
            if reg_orders:
                # Separate queued/running links keep the selected status actionable.
                for state, label in (('queued','注册排队中'),('running','注册执行中')):
                    todo('registration_'+state,label,registration[state],destination('/orders','registration',status=state),'可进入订单查看逐个交付进度。','info')
            if reg_enabled('accounts'):
                todo('accounts','可领取账号',available['available'],destination('/accounts','registration'),'交付后 72 小时内可领取，请及时保存。','success','个')
                todo('expiring','24 小时内到期',available['expiring'],destination('/accounts','registration',order_id=available['next_order_id']),'进入最早到期的订单领取并保存账号。','warning','个')
            wallet = q.one('SELECT balance-frozen AS available,frozen FROM users WHERE id=$1',user['id']) if flags['wallet'] else None
            from .records import ANN
            announcements = q.all(ANN+' ORDER BY sort,created_at DESC LIMIT 3')
            result = dict(wallet=wallet,announcements=announcements,permissions=dict(orders=flags['orders'],register=reg_enabled('register'),registration_orders=reg_orders,accounts=reg_enabled('accounts')))
        if admin:
            for item in attention:
                if item['key']!='risks':item['href']+='&include_admin='+str(include_admin).lower()
        result.update(queues=queues,attention=attention,recent_orders=recent,generated_at=q.scalar('SELECT now()'),
                      active=bool(sub['queued']+sub['running']+sub['settling']+registration['queued']+registration['running']+registration['review']))
        return result
