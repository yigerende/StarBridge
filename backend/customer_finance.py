from .listing import order_by
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
def customers(request:Request,q:str='',page:int=1,page_size:int=10,sort_by:str='created_at',sort_dir:str='desc'):
    start,end=period(request);pagination(page,page_size)
    q=q.strip();require(len(q)<=200,'搜索内容不能超过 200 个字符')
    exclude=request.query_params.get('include_admin')!='true'
    scope="(NOT $3::bool OR u.role<>'admin') AND ($4='' OR strpos(lower(u.email),lower($4))>0 OR strpos(lower(u.name),lower($4))>0 OR strpos(lower(u.id),lower($4))>0)"
    with db.transaction() as tx:
        tx.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        total=tx.scalar('SELECT count(*) FROM users u WHERE '+scope,start,end,exclude,q)
        page=min(page,max(1,(total+page_size-1)//page_size))
        aggregate='SELECT l.user_id,'+TOTALS+' FROM ledger l WHERE '+TIME+' GROUP BY l.user_id'
        fields=('credited','adjusted','rewarded','charged','returned','registration','subscription','refund')
        amounts=','.join('coalesce(s.'+k+',0) AS '+k for k in fields)
        ordering=order_by(sort_by,sort_dir,{**{k:'coalesce(s.'+k+',0)' for k in fields},'settled':'coalesce(s.charged,0)-coalesce(s.returned,0)','available':'u.balance-u.frozen','frozen':'u.frozen','email':'u.email','created_at':'u.created_at'},'created_at','u.id')
        rows=tx.all('SELECT u.id,u.email,u.name,u.role,u.status,u.deleted_at,u.balance,u.frozen,'+amounts+' FROM users u LEFT JOIN ('+aggregate+') s ON s.user_id=u.id WHERE '+scope+' ORDER BY '+ordering+' LIMIT $5 OFFSET $6',start,end,exclude,q,page_size,(page-1)*page_size)

    return dict(items=rows,total=total,page=page,page_size=page_size,has_more=page*page_size<total,start=start,end=end)


@router.get('/{id}')
def customer_ledger(id:str,request:Request,page:int=1,page_size:int=10,q:str='',kind:str='',business:str='',sort_by:str='created_at',sort_dir:str='desc'):
    from .wallet import ledger_page
    start,end=period(request);pagination(page,page_size)
    user=db.one('SELECT id,email,name,role,status,deleted_at,balance,frozen FROM users WHERE id=$1',id)
    require(user,'账户不存在',404)
    from starlette.requests import Request as ReadRequest
    scoped=ReadRequest({**request.scope,'state':{**request.scope.get('state',{}),'user':user}})
    # Admin reports may see order references regardless of customer feature switches.
    # The projection itself has no administrative fields or credentials.
    data=ledger_page(scoped,q,business,kind,start,end,page,page_size,sort_by,sort_dir,admin_projection=True)
    totals=db.one('SELECT '+TOTALS+' FROM ledger l WHERE '+TIME+' AND l.user_id=$3',start,end,id)
    for row in data['items']:
        row['order_id']=row.get('registration_order_id') or row.get('refund_order_id') or (row.get('related_order') or {}).get('id')
    return dict(data,user={k:v for k,v in user.items() if k!='role'},totals=totals,start=start,end=end)
