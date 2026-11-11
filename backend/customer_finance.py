"""Administrator-only, read-only customer wallet reports."""
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Request
from . import db
from .common import administrator, require

router=APIRouter(prefix='/api/admin/finance/customers',dependencies=[Depends(administrator)])
TIME="l.created_at>=($1::date AT TIME ZONE 'Asia/Shanghai') AND l.created_at<(($2::date+1) AT TIME ZONE 'Asia/Shanghai')"
TOTALS="""COALESCE(sum(l.amount) FILTER(WHERE l.kind IN ('topup','redeem')),0) AS credited,
 COALESCE(sum(l.amount) FILTER(WHERE l.kind='adjustment'),0) AS adjusted,
 COALESCE(sum(l.amount) FILTER(WHERE l.kind IN ('invite_reward','invite_reversal')),0) AS rewarded,
 COALESCE(sum(-l.amount) FILTER(WHERE l.kind='charge'),0) AS charged,
 COALESCE(sum(l.amount) FILTER(WHERE l.kind='refund'),0) AS returned,
 COALESCE(sum(-l.amount) FILTER(WHERE l.kind IN ('charge','refund') AND l.business='registration'),0) AS registration,
 COALESCE(sum(-l.amount) FILTER(WHERE l.kind IN ('charge','refund') AND l.business='subscription'),0) AS subscription,
 COALESCE(sum(-l.amount) FILTER(WHERE l.kind IN ('charge','refund') AND l.business='refund'),0) AS refund"""


def period(request):
    today=datetime.now(timezone(timedelta(hours=8))).date()
    start=request.query_params.get('start') or (today-timedelta(days=29)).isoformat()
    end=request.query_params.get('end') or today.isoformat()
    try:first,last=date.fromisoformat(start),date.fromisoformat(end)
    except ValueError:require(False,'统计日期格式无效')
    require(last>=first and (last-first).days<=3660 and last.year<9999,'统计日期范围无效')
    return start,end


def pagination(page,size):
    require(1<=page<=1000000 and size in (10,25,50,100,500),'分页参数无效')


@router.get('')
def customers(request:Request,q:str='',page:int=1,page_size:int=10):
    start,end=period(request);pagination(page,page_size)
    q=q.strip();require(len(q)<=200,'搜索内容不能超过 200 个字符')
    exclude=request.query_params.get('include_admin')!='true'
    scope="(NOT $3::bool OR u.role<>'admin') AND ($4='' OR strpos(lower(u.email),lower($4))>0 OR strpos(lower(u.name),lower($4))>0 OR strpos(lower(u.id),lower($4))>0)"
    with db.transaction() as tx:
        tx.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        total=tx.scalar('SELECT count(*) FROM users u WHERE '+scope,start,end,exclude,q)
        page=min(page,max(1,(total+page_size-1)//page_size))
        rows=tx.all('SELECT u.id,u.email,u.name,u.role,u.status,u.deleted_at,u.balance,u.frozen FROM users u WHERE '+scope+' ORDER BY u.created_at DESC,u.id DESC LIMIT $5 OFFSET $6',start,end,exclude,q,page_size,(page-1)*page_size)
        sums=tx.all('SELECT l.user_id,'+TOTALS+' FROM ledger l WHERE '+TIME+' AND l.user_id=ANY($3) GROUP BY l.user_id',start,end,[r['id'] for r in rows])
        by_user={r.pop('user_id'):r for r in sums}
        empty={k:0 for k in ('credited','adjusted','rewarded','charged','returned','registration','subscription','refund')}
        for row in rows:row.update(by_user.get(row['id'],empty))
    return dict(items=rows,total=total,page=page,page_size=page_size,has_more=page*page_size<total,start=start,end=end)


@router.get('/{id}')
def customer_ledger(id:str,request:Request,page:int=1,page_size:int=25):
    start,end=period(request);pagination(page,page_size)
    with db.transaction() as tx:
        tx.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        user=tx.one('SELECT id,email,name,status,deleted_at,balance,frozen FROM users WHERE id=$1',id)
        require(user,'账户不存在',404)
        totals=tx.one('SELECT '+TOTALS+',count(*) AS entries FROM ledger l WHERE '+TIME+' AND l.user_id=$3',start,end,id)
        total=totals.pop('entries');page=min(page,max(1,(total+page_size-1)//page_size))
        # Compute prior balances before applying date/page filters.
        rows=tx.all("""WITH history AS (
          SELECT l.id,l.kind,l.amount,l.balance_after,l.frozen_after,l.available_change,l.frozen_change,l.reference,
           l.business,l.order_id,l.registration_order_id,l.refund_order_id,l.description,l.created_at,
           lag(l.balance_after) OVER(ORDER BY l.id) previous_balance,lag(l.frozen_after) OVER(ORDER BY l.id) previous_frozen
          FROM ledger l WHERE l.user_id=$3
        ) SELECT l.id,l.kind,l.amount,l.balance_after,l.frozen_after,l.description,l.created_at,
          CASE WHEN l.kind IN ('topup','redeem','adjustment','invite_reward','invite_reversal') THEN 'shared' ELSE l.business END AS business,
          COALESCE(l.order_id,l.registration_order_id,l.refund_order_id) AS order_id,
          COALESCE(l.available_change,CASE WHEN l.previous_balance IS NOT NULL THEN (l.balance_after-l.frozen_after)-(l.previous_balance-l.previous_frozen)
            WHEN l.kind='freeze' THEN -o.fee WHEN l.kind='unfreeze' THEN o.fee
            WHEN l.kind='charge' AND l.reference LIKE 'payment:%' THEN 0 ELSE l.amount END) AS available_delta,
          COALESCE(l.frozen_change,CASE WHEN l.previous_frozen IS NOT NULL THEN l.frozen_after-l.previous_frozen
            WHEN l.kind='freeze' THEN o.fee WHEN l.kind='unfreeze' THEN -o.fee
            WHEN l.kind='charge' AND l.reference LIKE 'payment:%' THEN l.amount ELSE 0 END) AS frozen_delta
          FROM history l LEFT JOIN orders o ON o.id=l.order_id AND o.user_id=$3 WHERE """+TIME+' ORDER BY l.id DESC LIMIT $4 OFFSET $5',start,end,id,page_size,(page-1)*page_size)
    return dict(user=user,totals=totals,items=rows,total=total,page=page,page_size=page_size,has_more=page*page_size<total,start=start,end=end)
