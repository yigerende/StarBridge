"""Read-only finance reports from the unified ledger; never execute business work."""
import csv
import io
from datetime import date, datetime, time, timedelta
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from . import db
from .common import administrator, require
from .listing import order_by, PAGE_SIZES
from .report_period import ZONE, report_period

router = APIRouter(prefix='/api/admin/finance/reports', dependencies=[Depends(administrator)])
BUSINESSES = ('registration', 'subscription', 'refund')
NAMES = dict(shared='账户收支', registration='注册', subscription='订阅', refund='代退')
KINDS = dict(topup='充值', redeem='兑换入账', adjustment='人工调整', invite_reward='伙伴奖励',
             invite_reversal='奖励撤回', freeze='冻结', unfreeze='释放', charge='服务扣费', refund='服务退费')
MONEY = ('topups', 'redeemed', 'added', 'debited', 'rewarded', 'charged', 'returned', 'used',
         'freeze_added', 'released', 'registration', 'subscription', 'refund')
COUNTS = ('entries', 'settled_count', 'applications', 'queued', 'processing', 'succeeded',
          'partial', 'attention', 'failed', 'cancelled', 'delivered')
TOTAL_FIELDS = MONEY + COUNTS + ('balance', 'frozen', 'available')


def filters(request):
    args = request.query_params
    period = args.get('period', 'day')
    if period == 'custom':
        try:
            first, last = date.fromisoformat(args.get('start', '')), date.fromisoformat(args.get('end', ''))
            require(1900 <= first.year <= last.year <= 9998 and 0 <= (last-first).days <= 365,
                    '自定义日期须在 366 天内')
        except ValueError:
            require(False, '请选择有效日期')
        begin, until = datetime.combine(first, time(), ZONE), datetime.combine(last+timedelta(days=1), time(), ZONE)
    else:
        begin, until = report_period(period, args.get('date', ''))
    excluded = list(dict.fromkeys(args.getlist('exclude_user_id')))
    require(len(excluded) <= 200 and all(0 < len(x) <= 100 for x in excluded), '最多排除 200 个有效账户')
    search, status = args.get('q', '').strip(), args.get('status', '')
    require(len(search) <= 200 and status in ('', 'active', 'disabled', 'deleted'), '账户筛选无效')
    require(args.get('include_admin', 'false') in ('true', 'false'), '管理员筛选无效')
    require(args.get('active_only', 'false') in ('true', 'false'), '发生额筛选无效')
    return dict(period=period, start=begin.date().isoformat(), end=(until-timedelta(days=1)).date().isoformat(),
                begin=begin, until=until, q=search, status=status, excluded=excluded,
                include_admin=args.get('include_admin') == 'true', active_only=args.get('active_only') == 'true')


def meta(f):
    return {k: f[k] for k in ('period', 'start', 'end', 'include_admin', 'active_only')} | {
        'timezone': 'Asia/Shanghai', 'generated_at': datetime.now(ZONE).isoformat()}


def page_params(page, size):
    require(1 <= page <= 1000000 and size in PAGE_SIZES, '分页须为 10 / 50 / 100 / 500 条')


# Amounts remain integer cents throughout. Window deltas use the complete history
# before applying the reporting dates; a cross-midnight hold is not lost.
BASE = """WITH selected AS MATERIALIZED (
 SELECT u.id,u.email,u.name,u.role,u.status,u.deleted_at,u.balance,u.frozen,u.balance-u.frozen AS available
 FROM users u WHERE ($3::bool OR u.role<>'admin') AND NOT (u.id=ANY($4::text[]))
 AND ($5='' OR strpos(lower(u.email||' '||u.name||' '||u.id),lower($5))>0)
 AND ($6='' OR $6='deleted' AND u.deleted_at IS NOT NULL
   OR u.deleted_at IS NULL AND ($6='active' AND u.status='active' OR $6='disabled' AND u.status<>'active'))
), history AS (
 SELECT l.id,l.user_id,l.kind,l.amount,l.balance_after,l.frozen_after,l.created_at,l.description,
 COALESCE(l.registration_order_id,l.refund_order_id,l.order_id) AS order_id,
 CASE WHEN l.kind IN ('topup','redeem','adjustment','invite_reward','invite_reversal') THEN 'shared' ELSE l.business END AS business,
 COALESCE(l.frozen_change,l.frozen_after-lag(l.frozen_after,1,0::bigint) OVER (PARTITION BY l.user_id ORDER BY l.id)) AS frozen_delta,
 l.available_change
 FROM ledger l JOIN selected u ON u.id=l.user_id
), entries AS MATERIALIZED (
 SELECT h.*,COALESCE(available_change,amount-frozen_delta) AS available_delta FROM history h
 WHERE h.created_at >= $1 AND h.created_at < $2
) """

FUNDS = """COALESCE(sum(amount) FILTER(WHERE kind='topup'),0) AS topups,
 COALESCE(sum(amount) FILTER(WHERE kind='redeem'),0) AS redeemed,
 COALESCE(sum(amount) FILTER(WHERE kind='adjustment' AND amount>0),0) AS added,
 COALESCE(sum(-amount) FILTER(WHERE kind='adjustment' AND amount<0),0) AS debited,
 COALESCE(sum(amount) FILTER(WHERE kind IN ('invite_reward','invite_reversal')),0) AS rewarded,
 COALESCE(sum(-amount) FILTER(WHERE kind='charge'),0) AS charged,
 COALESCE(sum(amount) FILTER(WHERE kind='refund'),0) AS returned,
 COALESCE(sum(-amount) FILTER(WHERE kind IN ('charge','refund')),0) AS used,
 COALESCE(sum(GREATEST(frozen_delta,0)) FILTER(WHERE kind='freeze'),0) AS freeze_added,
 COALESCE(sum(GREATEST(-frozen_delta,0)) FILTER(WHERE kind='unfreeze'),0) AS released,
 COALESCE(sum(-amount) FILTER(WHERE kind IN ('charge','refund') AND business='registration'),0) AS registration,
 COALESCE(sum(-amount) FILTER(WHERE kind IN ('charge','refund') AND business='subscription'),0) AS subscription,
 COALESCE(sum(-amount) FILTER(WHERE kind IN ('charge','refund') AND business='refund'),0) AS refund,
 count(*) AS entries,
 count(DISTINCT (business,COALESCE(order_id,id::text))) FILTER(WHERE kind='charge') AS settled_count"""

REQUESTS = """count(*) AS applications,count(*) FILTER(WHERE state='queued') AS queued,
 count(*) FILTER(WHERE state='processing') AS processing,count(*) FILTER(WHERE state='succeeded') AS succeeded,
 count(*) FILTER(WHERE state='partial') AS partial,count(*) FILTER(WHERE state='attention') AS attention,
 count(*) FILTER(WHERE state='failed') AS failed,count(*) FILTER(WHERE state='cancelled') AS cancelled,
 COALESCE(sum(delivered),0) AS delivered"""


def args(f):
    return (f['begin'], f['until'], f['include_admin'], f['excluded'], f['q'], f['status'], f['active_only'])


def cte(tx):
    # Financial history includes deleted orders/accounts; deletion cannot erase
    # settlement. Subscription completion is payment success, independent of renewal.
    sources = """SELECT o.id,o.user_id,'subscription'::text AS business,
 CASE WHEN o.status='succeeded' THEN 'succeeded' WHEN o.status='queued' THEN 'queued'
 WHEN o.status IN ('failed','cancelled') THEN o.status WHEN o.status='review' THEN 'attention' ELSE 'processing' END AS state,
 0::bigint AS delivered,o.created_at FROM orders o WHERE o.workflow='subscription'
 UNION ALL SELECT r.id,r.user_id,'refund',CASE WHEN r.status IN ('failed','banned') THEN 'failed'
 WHEN r.status='verifying' THEN 'processing' ELSE r.status END,0::bigint,r.created_at FROM refunds r"""
    if tx.scalar("SELECT to_regclass('platform_orders')"):
        sources += """ UNION ALL SELECT o.id,p.xufu_user_id,'registration',CASE WHEN o.status='completed' THEN 'succeeded'
 WHEN o.status='running' THEN 'processing' WHEN o.status='review' THEN 'attention' ELSE o.status END,
 o.delivered::bigint,o.created_at FROM platform_orders o JOIN platform_users p ON p.id=o.user_id"""
    funds_fields = MONEY + ('entries', 'settled_count')
    order_fields = tuple(k for k in COUNTS if k not in ('entries', 'settled_count'))
    return BASE + ', applications AS MATERIALIZED (SELECT a.* FROM (' + sources + ''') a JOIN selected u ON u.id=a.user_id
 WHERE a.created_at >= $1 AND a.created_at < $2), funds AS (SELECT user_id,'''+FUNDS+''' FROM entries GROUP BY user_id),
 requests AS (SELECT user_id,'''+REQUESTS+''' FROM applications GROUP BY user_id), report AS MATERIALIZED (
 SELECT u.*,''' + ','.join(f'COALESCE(f.{k},0) AS {k}' for k in funds_fields) + ',' + ','.join(
        f'COALESCE(r.{k},0) AS {k}' for k in order_fields) + ''' FROM selected u LEFT JOIN funds f ON f.user_id=u.id
 LEFT JOIN requests r ON r.user_id=u.id WHERE NOT $7::bool OR COALESCE(f.entries,0)>0 OR COALESCE(r.applications,0)>0) '''


ACCOUNT_SORTS = {k: k for k in ('email', 'name', 'topups', 'redeemed', 'added', 'debited', 'freeze_added', 'released',
                               'used', 'settled_count', 'registration', 'subscription', 'refund', 'frozen', 'available',
                               'applications', 'succeeded', 'attention')}
ACCOUNT_SORTS['inflow'] = 'topups+redeemed+added'


def accounts_data(f, page=1, page_size=10, sort_by='used', sort_dir='desc', exporting=False):
    page_params(page, page_size)
    ordering = order_by(sort_by, sort_dir, ACCOUNT_SORTS, 'used')
    with db.transaction() as tx:
        tx.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        sql, values = cte(tx), args(f)
        summary = tx.one(sql+'SELECT count(*) AS customers,'+','.join(
            f'COALESCE(sum({k}),0) AS {k}' for k in TOTAL_FIELDS)+' FROM report', *values)
        total = summary['customers']
        if exporting:
            require(total <= 50000, '导出超过 50,000 位客户，请缩小筛选范围')
            page_size, page = max(1, total), 1
        page = min(page, max(1, (total+page_size-1)//page_size))
        if exporting:
            rows = tx.all(sql+'SELECT * FROM report ORDER BY '+ordering+' LIMIT $8', *values, page_size)
            return dict(items=rows, **meta(f))
        granularity = 'month' if f['period'] == 'year' else 'day'
        breakdown = sql + ', by_funds AS (SELECT business,'+FUNDS+" FROM entries WHERE user_id IN (SELECT id FROM report) GROUP BY business), by_orders AS (SELECT business,"+REQUESTS+" FROM applications WHERE user_id IN (SELECT id FROM report) GROUP BY business) "
        business_fields = ','.join('COALESCE(f.'+k+',0) AS '+k for k in MONEY+('settled_count',))+','+','.join('COALESCE(o.'+k+',0) AS '+k for k in COUNTS if k not in ('entries','settled_count'))
        data = tx.one(breakdown+"SELECT (SELECT COALESCE(jsonb_agg(x),'[]') FROM (SELECT * FROM report ORDER BY "+ordering+" LIMIT $8 OFFSET $9) x) AS items,"+
                      "(SELECT COALESCE(jsonb_agg(x),'[]') FROM (SELECT b.business,"+business_fields+
                      " FROM (VALUES ('registration',1),('subscription',2),('refund',3)) b(business,rank) LEFT JOIN by_funds f ON f.business=b.business LEFT JOIN by_orders o ON o.business=b.business ORDER BY b.rank) x) AS businesses,"+
                      "(SELECT COALESCE(jsonb_agg(x),'[]') FROM (SELECT to_char(date_trunc($10,created_at AT TIME ZONE 'Asia/Shanghai'),'YYYY-MM-DD') AS day,"+FUNDS+
                      ' FROM entries WHERE user_id IN (SELECT id FROM report) GROUP BY 1 ORDER BY 1) x) AS trend',
                      *values, page_size, (page-1)*page_size, granularity)
        rows, businesses, trend = data['items'], data['businesses'], data['trend']
    return dict(items=rows, total=total, page=page, page_size=page_size, summary=summary,
                businesses=businesses, trend=trend, trend_period=granularity, **meta(f))


@router.get('')
def accounts(request: Request, page: int=1, page_size: int=10, sort_by: str='used', sort_dir: str='desc'):
    return accounts_data(filters(request), page, page_size, sort_by, sort_dir)


@router.get('/options')
def options(q: str='', include_admin: bool=False, page: int=1):
    require(len(q) <= 200 and 1 <= page <= 1000000, '账户筛选无效')
    where = " FROM users WHERE ($1::bool OR role<>'admin') AND ($2='' OR strpos(lower(email||' '||name||' '||id),lower($2))>0)"
    with db.transaction() as tx:
        tx.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        total = tx.scalar('SELECT count(*)'+where, include_admin, q.strip())
        rows = tx.all('SELECT id,name,email'+where+' ORDER BY email,id LIMIT 50 OFFSET $3', include_admin, q.strip(), (page-1)*50)
    return dict(items=rows, total=total)


LEDGER_SORTS = {k: k for k in ('created_at', 'email', 'kind', 'business', 'amount', 'available_delta', 'frozen_delta', 'balance_after')}


def ledger_data(f, *, page=1, page_size=10, sort_by='created_at', sort_dir='desc', business='', kind='',
                user_id='', search='', exporting=False):
    page_params(page, page_size)
    require(business in ('', 'shared', *BUSINESSES) and kind in ('', *KINDS), '流水筛选无效')
    require(len(search) <= 200 and len(user_id) <= 100, '流水搜索内容过长')
    ordering = order_by(sort_by, sort_dir, LEDGER_SORTS, 'created_at')
    with db.transaction() as tx:
        tx.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        sql = cte(tx)+''', filtered AS (SELECT e.*,u.email,u.name FROM entries e JOIN report u ON u.id=e.user_id
 WHERE ($8='' OR e.business=$8) AND ($9='' OR e.kind=$9) AND ($10='' OR e.user_id=$10)
 AND ($11='' OR strpos(lower(COALESCE(e.order_id,'')||' '||e.description||' '||e.id::text),lower($11))>0)) '''
        values = (*args(f), business, kind, user_id, search.strip())
        summary = tx.one(sql+'SELECT count(*) AS total,COALESCE(sum(amount),0) AS net,'
                         'COALESCE(sum(available_delta),0) AS available,COALESCE(sum(frozen_delta),0) AS frozen FROM filtered', *values)
        total = summary['total']
        if exporting:
            require(total <= 50000, '导出超过 50,000 条流水，请缩小筛选范围')
            page, page_size = 1, max(1, total)
        page = min(page, max(1, (total+page_size-1)//page_size))
        rows = tx.all(sql+'SELECT id,user_id,email,name,kind,business,amount,available_delta,frozen_delta,'
                      'balance_after,frozen_after,order_id,description,created_at FROM filtered ORDER BY '+ordering+
                      ' LIMIT $12 OFFSET $13', *values, page_size, (page-1)*page_size)
    return dict(items=rows, total=total, page=page, page_size=page_size, summary=summary, **meta(f))


@router.get('/ledger')
def ledger(request: Request, page: int=1, page_size: int=10, sort_by: str='created_at', sort_dir: str='desc',
           business: str='', kind: str='', user_id: str='', search: str=''):
    return ledger_data(filters(request), page=page, page_size=page_size, sort_by=sort_by, sort_dir=sort_dir,
                       business=business, kind=kind, user_id=user_id, search=search)


def csv_response(columns, rows, filename):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([title for _, title, _ in columns])
    for row in rows:
        cells = []
        for key, _, money in columns:
            value = row.get(key, '')
            if money:
                # Numeric cells are generated internally; no formula-capable input.
                from decimal import Decimal
                value = format(Decimal(value or 0)/100, '.2f')
            elif isinstance(value, datetime):
                value = value.astimezone(ZONE).isoformat()
            elif isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')) or isinstance(value, str) and value.startswith(('\t', '\r', '\n')):
                value = "'"+value
            cells.append(value)
        writer.writerow(cells)
    return Response(b'\xef\xbb\xbf'+output.getvalue().encode('utf-8'), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'no-store'})


@router.get('/export')
def export(request: Request, view: str='accounts', sort_by: str='', sort_dir: str='desc', business: str='',
           kind: str='', user_id: str='', search: str=''):
    f = filters(request)
    require(view in ('accounts', 'ledger'), '导出类型无效')
    if view == 'accounts':
        data = accounts_data(f, sort_by=sort_by or 'used', sort_dir=sort_dir, exporting=True)
        fields = [('email','账户',False),('name','称呼',False),('topups','充值积分',True),('redeemed','兑换积分',True),
                  ('added','人工加款积分',True),('debited','人工扣减积分',True),('rewarded','伙伴奖励净额',True),
                  ('freeze_added','期间冻结积分',True),('released','期间释放积分',True),('charged','服务扣费积分',True),
                  ('returned','服务退费积分',True),('used','净消费积分',True),('registration','注册净消费',True),
                  ('subscription','订阅净消费',True),('refund','代退净消费',True),('settled_count','结算订单数',False),
                  ('balance','当前总余额',True),('available','当前可用余额',True),('frozen','当前冻结',True),
                  *[(k, n, False) for k,n in zip(('applications','queued','processing','succeeded','partial','attention','failed','cancelled','delivered'),
                    ('期间提交订单','排队','办理中','完成','部分交付','需跟进','失败','撤回','注册交付账号'))]]
    else:
        data = ledger_data(f, sort_by=sort_by or 'created_at', sort_dir=sort_dir, business=business, kind=kind,
                           user_id=user_id, search=search, exporting=True)
        for row in data['items']:
            row['business'], row['kind'] = NAMES.get(row['business'],row['business']), KINDS.get(row['kind'],row['kind'])
        fields = [('created_at','时间（北京时间）',False),('email','账户',False),('name','称呼',False),('kind','类型',False),
                  ('business','业务',False),('amount','余额变动（积分）',True),('available_delta','可用变动',True),
                  ('frozen_delta','冻结变动',True),('balance_after','变动后总余额',True),('frozen_after','变动后冻结',True),
                  ('order_id','关联订单',False),('description','说明',False)]
    for row in data['items']:
        row.update(start=f['start'], end=f['end'])
    return csv_response(fields+[('start','开始日期',False),('end','结束日期',False)],data['items'], f'starbridge-{view}-{f["start"]}-{f["end"]}.csv')
