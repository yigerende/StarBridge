from .listing import order_by
"""Stable, account-scoped API for customer integrations."""
from fastapi import APIRouter, Depends, Query, Request

from . import db, membership, records, renewals, wallet
from .common import body, config, current, member, require
from .product_ids import public_order
from .product_availability import require_product_flow
from . import regions
from .customer_contract import OrderRequest, RenewalRequest, TopupRequest, prepare, replay, order_request_matches

router = APIRouter(prefix='/api/v1', dependencies=[Depends(member)])


@router.post('/orders')
async def create_order(request: Request):
    payload = prepare(request, OrderRequest, await body(request))
    old = db.one('SELECT id,status,request_hash,deleted_at FROM orders WHERE user_id=$1 AND idempotency_key=$2',
                 current(request)['id'], request.headers['Idempotency-Key'])
    if old:
        require(old['deleted_at'] is None, '原订单已删除；此请求标识不能再次下单', 409)
        require(order_request_matches(request,old['request_hash']),
                '同一 Idempotency-Key 不能用于不同请求', 409)
        return {'id': old['id'], 'status': old['status'], 'replayed': True}
    from . import subscription, subscription_cslive_go, subscription_cslive_full
    flows = {module.FLOW: module for module in (subscription, subscription_cslive_go, subscription_cslive_full)}
    selected = flows.get(config('payment').get('subscription_flow'))
    require(selected is not None, '当前订阅办理不可用，请联系服务支持', 503)
    require(not payload.get('promo_code') or selected.FLOW=='cslive_go_direct_pay', '当前流程不支持自定义优惠码')
    require_product_flow(payload['product_id'], selected.FLOW)
    regions.selected(payload['country'],selected.FLOW)
    return await selected.create_order(request)


@router.post('/renewals')
async def create_renewal(request: Request):
    prepare(request, RenewalRequest, await body(request))
    old = replay(db, request, 'renewal')
    if old:
        old['status'] = db.scalar('SELECT status FROM renewal_tasks WHERE id=$1 AND user_id=$2',
                                  old['id'], current(request)['id'])
        return old
    require(config('payment').get('enabled') is True, '续费办理暂未开放', 403)
    return await renewals.create_task(request)


def page_result(items, total, page, page_size):
    return {'items': items, 'page': page, 'page_size': page_size,
            'total': total, 'has_more': page * page_size < total}


@router.get('/renewals')
def renewal_list(request:Request,q:str='',status:str='',page:int=Query(1,ge=1,le=100000),page_size:int=Query(50,ge=1,le=500),sort_by:str='created_at',sort_dir:str='desc'):
    return renewals.tasks(request,q,status,page,page_size,sort_by,sort_dir)


@router.get('/renewals/{id}')
def renewal_detail(id: str, request: Request):
    return renewals.task(id, request)


@router.get('/me')
def me(request: Request):
    return membership.me(request)


@router.get('/overview')
def overview(request: Request):
    result = records.overview(request)
    for row in result['recent_orders']:public_order(row, request)
    return result


@router.get('/wallet/ledger')
def ledger(request:Request,q:str='',business:str='',kind:str='',start:str='',end:str='',page:int=Query(1,ge=1,le=100000),page_size:int=Query(50,ge=1,le=500),sort_by:str='created_at',sort_dir:str='desc'):
    return wallet.ledger_page(request,q,business,kind,start,end,page,page_size,sort_by,sort_dir)


@router.get('/topups')
def topups(request: Request, status: str = '', page: int = Query(1, ge=1, le=100000),
           page_size: int = Query(50, ge=1, le=500),q:str='',sort_by:str='created_at',sort_dir:str='desc'):
    require(status in ('', 'pending', 'approved', 'rejected', 'cancelled'), '充值状态无效')
    require(len(q)<=200,'搜索内容过长')
    ordering=order_by(sort_by,sort_dir,{k:k for k in ('amount','status','created_at','updated_at')},'created_at')
    where = " FROM topups WHERE user_id=$1 AND ($2='' OR status=$2) AND ($3='' OR id ILIKE '%'||$3||'%' OR note ILIKE '%'||$3||'%')"
    items = db.all('SELECT id,amount,method,proof,status,note,created_at,updated_at' + where +
                   ' ORDER BY '+ordering+' LIMIT $4 OFFSET $5',
                   current(request)['id'], status,q,page_size, (page - 1) * page_size)
    total = db.scalar('SELECT count(*)' + where, current(request)['id'], status,q)
    return page_result(items, total, page, page_size)


@router.post('/topups')
async def create_topup(request: Request):
    prepare(request, TopupRequest, await body(request))
    old = replay(db, request, 'topup')
    return old if old else await wallet.topup(request)


@router.post('/topups/{id}/cancel')
def cancel_topup(id: str, request: Request):
    with db.transaction() as q:
        row = q.one('SELECT status FROM topups WHERE id=$1 AND user_id=$2 FOR UPDATE',
                    id, current(request)['id'])
        require(row, '充值申请不存在', 404)
        require(row['status'] in ('pending', 'cancelled'), '充值申请已审核，不能取消', 409)
        if row['status'] == 'pending':
            q.execute("UPDATE topups SET status='cancelled',updated_at=now() WHERE id=$1", id)
    return {'ok': True}
