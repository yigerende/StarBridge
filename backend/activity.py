from .listing import order_by, pagination, result as list_result
"""Paginated registration, subscription and refund history in one snapshot."""
from datetime import date, datetime, time, timedelta
from fastapi import APIRouter, Depends, Query, Request
from . import db, order_timing
from .common import administrator, current, member, require
from .dashboard import order_source, destination
from .features import capabilities
from .report_period import ZONE

router=APIRouter(prefix='/api',dependencies=[Depends(member)])
admin_router=APIRouter(prefix='/api/admin',dependencies=[Depends(administrator)])


@admin_router.get('/renewals/{id}')
def renewal_record(id:str):
    row=db.one('SELECT t.id,t.email,t.status,t.message,t.created_at,t.updated_at,u.email AS owner FROM renewal_tasks t JOIN users u ON u.id=t.user_id WHERE t.id=$1',id)
    require(row,'记录不存在',404)
    return {'task':row,'events':db.all('SELECT status,message,created_at FROM renewal_events WHERE task_id=$1 ORDER BY id',id)}


def listing(user,admin=False,search='',business='',state='',start='',end='',page=1,page_size=25,include_admin=True,sort_by='created_at',sort_dir='desc'):
    require(len(search)<=200 and business in ('','registration','subscription','refund'),'筛选条件无效')
    require(state in ('','active','completed','partial','attention','failed','cancelled'),'状态筛选无效')
    require(1<=page<=1000000 and 1<=page_size<=500,'分页参数无效')
    try:
        first=date.fromisoformat(start) if start else None
        last=date.fromisoformat(end) if end else None
        require(not first or not last or first<=last,'结束日期不能早于开始日期')
        require(not last or last.year<9999,'日期范围无效')
        begin=datetime.combine(first,time(),ZONE) if first else None
        until=datetime.combine(last+timedelta(days=1),time(),ZONE) if last else None
    except (TypeError,ValueError):require(False,'日期格式无效')
    pattern='%'+search.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    ordering=order_by(sort_by,sort_dir,{k:k for k in ('title','business','email','owner','created_at','updated_at','state','settled','frozen')},'created_at','business,id')
    flags=capabilities(user)
    with db.transaction() as q:
        q.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        registration=bool(q.scalar("SELECT to_regclass('registration_reservations')"))
        scope="($2::bool OR u.role<>'admin')" if admin else 'a.user_id=$1'
        # Settlement comes from actual ledger entries, not the order's quoted fee.
        source=order_source(registration)+''' UNION ALL SELECT t.id,t.user_id,u.email AS owner,'subscription'::text AS business,
          '关闭自动续费'::text AS title,t.email,t.status,'renewal'::text AS workflow,''::text AS activation,''::text AS renewal,
          1 AS quantity,0 AS delivered,0::bigint AS fee,t.created_at,t.updated_at,
          t.status IN ('disabled','no_subscription') AS completed,t.status='review' AS attention
          FROM renewal_tasks t JOIN users u ON u.id=t.user_id'''
        cte='WITH activity AS ('+source+'''), classified AS (
          SELECT a.*,CASE WHEN attention THEN 'attention' WHEN completed THEN 'completed'
            WHEN status='partial' THEN 'partial' WHEN status IN ('failed','banned') THEN 'failed'
            WHEN status='cancelled' THEN 'cancelled' ELSE 'active' END AS state
          FROM activity a), scoped AS (SELECT a.* FROM classified a JOIN users u ON u.id=a.user_id WHERE '''+scope+'''
          AND ($3='' OR a.business=$3) AND ($4='' OR a.state=$4)
          AND ($5::timestamptz IS NULL OR a.created_at >= $5) AND ($6::timestamptz IS NULL OR a.created_at < $6)
          AND (a.id ILIKE $7 OR a.email ILIKE $7 OR a.title ILIKE $7'''+(' OR a.owner ILIKE $7' if admin else '')+''')), funds AS (
          SELECT l.business,COALESCE(l.order_id,l.registration_order_id,l.refund_order_id) AS id,
            COALESCE(sum(-l.amount) FILTER(WHERE l.kind IN ('charge','refund')),0)::bigint AS settled,
            COALESCE(sum(CASE WHEN l.frozen_change IS NOT NULL THEN l.frozen_change
              WHEN l.kind='freeze' THEN a.fee WHEN l.kind IN ('unfreeze','charge') THEN -a.fee ELSE 0 END),0)::bigint AS frozen
          FROM ledger l JOIN scoped a ON a.workflow<>'renewal' AND a.user_id=l.user_id AND a.business=l.business AND a.id=COALESCE(l.order_id,l.registration_order_id,l.refund_order_id)
          GROUP BY l.business,COALESCE(l.order_id,l.registration_order_id,l.refund_order_id)), enriched AS (
          SELECT a.*,COALESCE(f.settled,0) AS settled,GREATEST(COALESCE(f.frozen,0),0) AS frozen
          FROM scoped a LEFT JOIN funds f ON a.workflow<>'renewal' AND f.business=a.business AND f.id=a.id) '''
        aggregate="""SELECT count(*) AS total,count(*) FILTER(WHERE state='active') AS active,
          count(*) FILTER(WHERE state='completed') AS completed,count(*) FILTER(WHERE state='partial') AS partial,
          count(*) FILTER(WHERE state='attention') AS attention,count(*) FILTER(WHERE state='failed') AS failed,
          count(*) FILTER(WHERE state='cancelled') AS cancelled,count(DISTINCT user_id) AS customers,
          COALESCE(sum(delivered) FILTER(WHERE business='registration'),0) AS delivered,
          COALESCE(sum(settled),0) AS settled,
          COALESCE(sum(frozen),0) AS frozen FROM enriched"""
        args=(user['id'],include_admin,business,state,begin,until,pattern,page_size,(page-1)*page_size)
        result=q.one(cte+"SELECT (SELECT to_jsonb(s) FROM ("+aggregate+") s) AS summary,"+
          "(SELECT coalesce(jsonb_agg(s),'[]') FROM (SELECT business,count(*) AS total,count(*) FILTER(WHERE state='active') AS active,COALESCE(sum(settled),0) AS settled FROM enriched GROUP BY business) s) AS businesses,"+
          "(SELECT coalesce(jsonb_agg(s),'[]') FROM (SELECT * FROM enriched ORDER BY "+ordering+" LIMIT $8 OFFSET $9) s) AS items",*args)
        order_timing.activity(result['items'],q)
        if admin:
            from .order_stage import attach
            attach(result['items'],q=q)
        for row in result['items']:
            row['href']=destination('/admin/orders' if admin else '/orders',row['business'],order_id=row['id']) if admin or flags['orders'] else None
            if row['workflow']=='renewal':row['href']=destination('/renewal',task_id=row['id']) if not admin and flags['renewal'] else None
            if not admin:
                row.pop('user_id',None);row.pop('owner',None)
                if not flags['wallet']:
                    for key in ('fee','settled','frozen'):row.pop(key,None)
        if not admin:result['summary'].pop('customers',None)
        if not admin and not flags['wallet']:
            for key in ('settled','frozen'):result['summary'].pop(key,None)
            for row in result['businesses']:row.pop('settled',None)
    result['businesses'].sort(key=lambda r:['registration','subscription','refund'].index(r['business']))
    return {**result,'total':result['summary']['total'],'page':page,'page_size':page_size,
            'has_more':page*page_size<result['summary']['total'],'can_view_orders':admin or flags['orders'],
            'show_fees':admin or flags['wallet'],'timezone':'Asia/Shanghai'}


@router.get('/activity')
@router.get('/v1/activity')
def customer_activity(request:Request,q:str='',business:str='',state:str='',start:str='',end:str='',
                      page:int=Query(1,ge=1,le=1000000),page_size:int=Query(25),sort_by:str='created_at',sort_dir:str='desc'):
    return listing(current(request),search=q,business=business,state=state,start=start,end=end,page=page,page_size=page_size,sort_by=sort_by,sort_dir=sort_dir)


@admin_router.get('/activity')
def admin_activity(request:Request,q:str='',business:str='',state:str='',start:str='',end:str='',
                   page:int=Query(1,ge=1,le=1000000),page_size:int=Query(25),include_admin:bool=True,sort_by:str='created_at',sort_dir:str='desc'):
    return listing(current(request),admin=True,search=q,business=business,state=state,start=start,end=end,page=page,page_size=page_size,include_admin=include_admin,sort_by=sort_by,sort_dir=sort_dir)
