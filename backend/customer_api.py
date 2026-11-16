"""Stable, account-scoped API for customer integrations."""
from fastapi import APIRouter, Depends, Query, Request

from . import db, membership, records, renewals, wallet
from .common import body, config, current, member, require
from .product_ids import public_order
from .product_availability import require_product_flow
from . import regions
from .customer_contract import OrderRequest, RenewalRequest, prepare, replay, order_request_matches

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
