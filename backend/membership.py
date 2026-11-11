from fastapi import APIRouter, Depends, Request
from . import db
from .common import *
from .product_ids import internal_id, public_id
from .product_availability import visible_products, require_product_flow

router=APIRouter(prefix='/api',dependencies=[Depends(member)])
SPEND="""SELECT l.user_id,l.order_id,GREATEST(0,-sum(l.amount))::bigint AS spent FROM ledger l JOIN orders o ON o.id=l.order_id AND o.user_id=l.user_id WHERE o.status='succeeded' AND ((l.kind='charge' AND l.amount<0) OR (l.kind='refund' AND l.amount>0)) GROUP BY l.user_id,l.order_id"""


def membership_from(user,spent,q=None,*,tiers=None,at=None):
    tiers=sorted(config('tiers',q)['items'] if tiers is None else tiers,key=lambda x:x['threshold'])
    tiers=[dict(t,code=t.get('code') or f'L{i+1}') for i,t in enumerate(tiers)]
    settled_spent=int(spent)
    active=bool(user.get('membership_code')) and (not user.get('membership_until') or user['membership_until']>(at or now()))
    adjustment=int(user.get('membership_growth_credit') or 0) if active else 0
    spent=max(0,settled_spent+adjustment)
    selected=tiers[0]
    for t in tiers:
        if spent>=t['threshold']:selected=t
    following=next((t for t in tiers if t['threshold']>selected['threshold']),None)
    return {'current':selected,'automatic':selected,'next':following,'spent':spent,'settled_spent':settled_spent,'growth_credit':adjustment,'remaining':following['threshold']-spent if following else 0,'progress':(spent-selected['threshold'])*100//(following['threshold']-selected['threshold']) if following else 100,'source':'manual' if active else 'automatic','until':user.get('membership_until') if active else None,'fee_override':user.get('fee_override'),'tiers':tiers}


def assign_membership(q,ids,code,until):
    """Top up one growth total; repeated saves preserve subsequent earned growth."""
    rows=q.all('SELECT id,membership_code,membership_until,membership_growth_credit FROM users WHERE id=ANY($1) AND deleted_at IS NULL ORDER BY id FOR UPDATE',sorted(ids))
    require(len(rows)==len(ids),'账户不存在',404)
    tier=next((t for t in config('tiers',q)['items'] if t['code']==code),None)
    require(not code or tier is not None,'会员等级不存在')
    sums={r['user_id']:int(r['spent']) for r in q.all('SELECT user_id,sum(spent) AS spent FROM ('+SPEND+') s WHERE user_id=ANY($1) GROUP BY user_id',ids)}
    at=now()
    for row in rows:
        credit=row['membership_growth_credit']
        if not code:credit=0
        elif row['membership_code']!=code or (row['membership_until'] and row['membership_until']<=at and (until is None or until>at)):
            credit=max(0,tier['threshold']-sums.get(row['id'],0))
        q.execute('UPDATE users SET membership_code=$2,membership_until=$3,membership_growth_credit=$4,updated_at=now() WHERE id=$1',row['id'],code,until if code else None,credit)


def membership_for(user_id,q=None):
    q=q or db
    user=q.one('SELECT membership_code,membership_until,membership_growth_credit,fee_override FROM users WHERE id=$1',user_id)
    require(user is not None,'用户不存在',404)
    spent=q.scalar('SELECT COALESCE(sum(s.spent),0) FROM ('+SPEND+') s WHERE s.user_id=$1',user_id)
    result=membership_from(user,int(spent),q)
    result['product_prices']={public_id(row['product_id']):row['fee'] for row in q.all('SELECT product_id,fee FROM user_product_prices WHERE user_id=$1',user_id)}
    result['service_prices']={row['service']:row['fee'] for row in q.all('SELECT service,fee FROM user_service_prices WHERE user_id=$1',user_id)}
    result['discount_services']=discount_services(q)
    return result


def discount_services(q=None):
    return {'registration_standard':True,'registration_oauth':False,'refund':True,'subscription':{},**config('membership_pricing',q)}


def membership_eligible(m,product_id):
    rules=m.get('discount_services',{})
    if product_id in ('registration_standard','registration_oauth','refund'):
        return rules.get(product_id,product_id!='registration_oauth') is True
    return rules.get('subscription',{}).get(public_id(product_id),True) is True


def fee_for(m,standard,product_id=None):
    if product_id in ('refund','registration_standard','registration_oauth'):
        override=m.get('service_prices',{}).get(product_id)
        return override if override is not None else (standard*m['current']['discount']+50)//100 if membership_eligible(m,product_id) else standard
    override=m.get('product_prices',{}).get(public_id(product_id))
    if override is not None:return override
    return m['fee_override'] if m['fee_override'] is not None else (standard*m['current']['discount']+50)//100 if membership_eligible(m,product_id) else standard


def fee_source(m,product_id):
    if product_id in ('refund','registration_standard','registration_oauth'):
        return 'account_override' if product_id in m.get('service_prices',{}) else 'membership' if membership_eligible(m,product_id) else 'standard'
    if public_id(product_id) in m.get('product_prices',{}):return 'product_override'
    return 'account_override' if m['fee_override'] is not None else 'membership' if membership_eligible(m,product_id) else 'standard'


@router.get('/me')
def me(request: Request):
    from .features import project_user, capabilities, navigation
    user=current(request);flags=capabilities(user)
    result={**project_user(user),'features':flags,'navigation':navigation(user),'membership':membership_for(user['id']) if flags['membership'] else None}
    if request.url.path=='/api/me':
        from .custom_menus import visible
        result['custom_menus']=visible(user)
    return result


@router.get('/membership')
@router.get('/v1/membership')
def membership(request: Request):
    m=membership_for(current(request)['id'])
    products=visible_products(db.all('SELECT id,name,description,fee,enabled FROM products ORDER BY sort'),config('payment').get('subscription_flow'))
    for p in products:
        p['member_fee']=fee_for(m,p['fee'],p['id']);p['saved']=p['fee']-p['member_fee'];p['price_source']=fee_source(m,p['id']);p['membership_eligible']=membership_eligible(m,p['id'])
        if request.url.path.startswith('/api/v1/'):p['id']=public_id(p['id'])
    growth=db.all('SELECT o.id,o.product_name,o.updated_at,s.spent FROM ('+SPEND+') s JOIN orders o ON o.id=s.order_id WHERE s.user_id=$1 AND o.deleted_at IS NULL ORDER BY o.updated_at DESC LIMIT 100',current(request)['id'])
    return {'membership':m,'products':products,'growth':growth,'unit':'cent_R','basis':'membership_growth'}


@router.get('/quote')
@router.get('/v1/quote')
def quote(request: Request,product_id:str=''):
    from .features import allowed
    if request.url.path.startswith('/api/v1/'):product_id=internal_id(product_id)
    with db.transaction() as q:
        product=q.one('SELECT fee FROM products WHERE id=$1 AND enabled',product_id)
        require(product is not None,'套餐不存在或已下架',404)
        m=membership_for(current(request)['id'],q)
        payment=config('payment',q)
        require_product_flow(product_id,payment.get('subscription_flow'))
        connected=payment.get('enabled') is True and payment.get('subscription_flow') in ('gener_direct_pay','cslive_go_direct_pay','cslive_full_go')
        return {'fee':fee_for(m,product['fee'],product_id),'standard':product['fee'],'membership':m if allowed('membership',current(request)) else None,'payment_connected':connected and allowed('subscribe',current(request)),'price_source':fee_source(m,product_id),'membership_eligible':membership_eligible(m,product_id)}
