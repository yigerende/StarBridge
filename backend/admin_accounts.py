from .listing import order_by, pagination, result as list_result
from fastapi import APIRouter,Depends,Request
from psycopg2.errors import UniqueViolation
from starlette.concurrency import run_in_threadpool
from . import redeem_codes
from . import db
from .common import *
from .wallet import credit
from .membership import membership_from,assign_membership,discount_services,SPEND
from .product_ids import internal_id, public_id
from .product_availability import visible_products

router=APIRouter(prefix='/api/admin',dependencies=[Depends(administrator)])


def delete_accounts(request, ids):
    require(isinstance(ids,list) and 1<=len(ids)<=500 and all(isinstance(x,str) and 1<=len(x)<=80 for x in ids),'请选择 1～500 个账户')
    ids=sorted(set(ids));deleted=[];skipped=[]
    with db.transaction() as q:
        q.execute('SELECT pg_advisory_xact_lock(1886001)')
        rows=q.all('SELECT id,email,role,status,deleted_at FROM users WHERE id=ANY($1) ORDER BY id FOR UPDATE',ids)
        admins=q.scalar("SELECT count(*) FROM users WHERE role='admin' AND status='active' AND deleted_at IS NULL")
        for row in rows:
            if row['deleted_at'] is not None:continue
            reason='不能删除当前登录的管理员' if row['id']==current(request)['id'] else '至少保留一个可用管理员' if row['role']=='admin' and row['status']=='active' and admins<=1 else ''
            if reason:
                skipped.append({'id':row['id'],'email':row['email'],'reason':reason});continue
            q.execute("UPDATE users SET deleted_at=now(),status='disabled',password_hash='',updated_at=now() WHERE id=$1",row['id'])
            q.execute('DELETE FROM sessions WHERE user_id=$1',row['id'])
            q.execute('UPDATE api_keys SET revoked=true,secret_enc=NULL WHERE user_id=$1',row['id'])
            q.execute('DELETE FROM email_challenges WHERE email=$1 OR user_id=$2',row['email'],row['id'])
            audit(q,request,'user.deleted',row['id'],'删除账户 '+row['email']+'；保留历史订单、资金及已受理任务')
            deleted.append(row['id'])
            if row['role']=='admin' and row['status']=='active':admins-=1
    return {'ok':True,'deleted':len(deleted),'deleted_ids':deleted,'skipped':skipped}


@router.post('/users/batch-delete')
async def batch_delete_users(request:Request):
    return delete_accounts(request,(await body(request)).get('ids'))


@router.delete('/users/{id}')
def delete_user(id:str,request:Request):
    return delete_accounts(request,[id])


@router.get('/users')
def users(q: str='', role: str='', status: str='', tier: str='', page: int=1, page_size: int=10, sort_by: str='created_at', sort_dir: str='desc'):
    q=q.strip()
    require(len(q)<=200,'搜索内容不能超过 200 个字符')
    require(role in ('','member','admin') and status in ('','active','disabled'),'账户筛选条件无效')
    require(1<=page<=1000000 and 1<=page_size<=500,'分页参数无效')
    with db.transaction() as tx:
        # Counts, page rows and effective membership share one read-only snapshot.
        tx.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        tiers=membership_from({},0,tx)['tiers']
        require(tier=='' or any(t['code']==tier for t in tiers),'会员等级不存在')
        tier_rows={'items':[dict(code=t['code'],threshold=t['threshold'],position=i) for i,t in enumerate(tiers)]}
        query="""WITH tiers AS (
            SELECT * FROM jsonb_to_recordset($5::jsonb->'items') AS t(code text,threshold bigint,position int)
        ), accounts AS (
            SELECT u.id,u.email,u.name,u.role,u.status,u.balance,u.frozen,u.fee_override,
                   u.membership_code,u.membership_until,u.membership_growth_credit,u.invite_code,u.created_at,
                   COALESCE(s.spent,0)::bigint AS spent
            FROM users u LEFT JOIN (SELECT user_id,sum(spent) AS spent FROM ("""+SPEND+""") s GROUP BY user_id) s ON s.user_id=u.id
            WHERE u.deleted_at IS NULL
              AND ($1='' OR strpos(lower(u.email),lower($1))>0 OR strpos(lower(u.name),lower($1))>0 OR strpos(lower(u.id),lower($1))>0)
              AND ($2='' OR u.role=$2) AND ($3='' OR u.status=$3)
        ), filtered AS (
            SELECT a.* FROM accounts a
            LEFT JOIN LATERAL (SELECT code FROM tiers WHERE threshold<=a.spent+CASE WHEN a.membership_code<>'' AND (a.membership_until IS NULL OR a.membership_until>$6) THEN a.membership_growth_credit ELSE 0 END OR position=0 ORDER BY threshold DESC,position DESC LIMIT 1) effective ON true
            WHERE $4='' OR effective.code=$4
        ) """
        args=(q,role,status,tier,tier_rows,now())
        total=tx.scalar(query+'SELECT count(*) FROM filtered',*args)
        page=min(page,max(1,(total+page_size-1)//page_size))
        ordering=order_by(sort_by,sort_dir,{'account':'f.email','email':'f.email','created_at':'f.created_at','role':'f.role','status':'f.status','balance':'(f.balance-f.frozen)','frozen':'f.frozen','used_balance':"(SELECT coalesce(-sum(amount),0) FROM ledger WHERE user_id=f.id AND kind IN ('charge','refund'))",'membership':"(f.spent+CASE WHEN f.membership_code<>'' AND (f.membership_until IS NULL OR f.membership_until>now()) THEN f.membership_growth_credit ELSE 0 END)",'orders':'orders','fee_override':'f.fee_override','membership_until':'f.membership_until'},'created_at','f.id')
        rows=tx.all(query+"SELECT f.*,(SELECT count(*) FROM orders o WHERE o.user_id=f.id AND o.deleted_at IS NULL) AS orders FROM filtered f ORDER BY "+ordering+" LIMIT $7 OFFSET $8",*args,page_size,(page-1)*page_size)
        ids=[row['id'] for row in rows]
        used={r['user_id']:int(r['amount']) for r in tx.all("SELECT user_id,-sum(amount) AS amount FROM ledger WHERE user_id=ANY($1) AND kind IN ('charge','refund') GROUP BY user_id",ids)}
        prices={};services={}
        for row in tx.all('SELECT user_id,product_id,fee FROM user_product_prices WHERE user_id=ANY($1)',ids):
            prices.setdefault(row['user_id'],{})[row['product_id']]=row['fee']
        for row in tx.all('SELECT user_id,service,fee FROM user_service_prices WHERE user_id=ANY($1)',ids):
            services.setdefault(row['user_id'],{})[row['service']]=row['fee']
        for row in rows:
            row['used_balance']=used.get(row['id'],0)
            row['membership']=membership_from(row,row['spent'],tiers=tiers,at=args[-1])
            row['product_prices']=prices.get(row['id'],{})
            row['service_prices']=services.get(row['id'],{})
        products=visible_products(tx.all('SELECT id,name,fee FROM products WHERE enabled ORDER BY sort,id'),config('payment',tx).get('subscription_flow'))
    return {'items':rows,'tiers':tiers,'products':products,'total':total,'page':page,'page_size':page_size,'has_more':page*page_size<total}


@router.post('/users/batch-settings')
async def batch_settings(request:Request):
    return await run_in_threadpool(batch_settings_value,await body(request),request)


def batch_settings_value(value,request):
    ids=value.get('ids');changes=value.get('changes')
    require(isinstance(ids,list) and 1<=len(ids)<=500 and all(isinstance(i,str) and 1<=len(i)<=80 for i in ids),'请选择 1～500 个账户')
    require(isinstance(changes,dict) and changes and set(changes)<={'fee_override','service_prices','product_prices','membership'},'请选择要调整的价格或会员')
    ids=sorted(set(ids))
    def price(fee):return None if fee is None else integer(fee,0,100000000,'专属价格须为有效的非负整数金额')
    with db.transaction() as q:
        q.execute('SELECT pg_advisory_xact_lock(782518860)')
        found=q.all('SELECT id FROM users WHERE id=ANY($1) AND deleted_at IS NULL ORDER BY id FOR UPDATE',ids)
        require(len(found)==len(ids),'部分账户已删除，请刷新列表后重新选择',409)
        if 'fee_override' in changes:
            q.execute('UPDATE users SET fee_override=$2 WHERE id=ANY($1)',ids,price(changes['fee_override']))
        count=int('fee_override' in changes)
        for field,table,key,allowed in (
            ('service_prices','user_service_prices','service',{'registration_standard','registration_oauth','refund'}),
            ('product_prices','user_product_prices','product_id',{p['id'] for p in visible_products(q.all('SELECT id FROM products WHERE enabled'),config('payment',q).get('subscription_flow'))})
        ):
            if field not in changes:continue
            values=changes[field]
            require(isinstance(values,dict) and len(values)<=200,'专属价格格式无效')
            normalized={}
            for code,fee in values.items():
                require(isinstance(code,str),'服务名称无效')
                code=internal_id(code) if field=='product_prices' else code
                require(code in allowed and code not in normalized,'请选择已开放的服务或套餐')
                normalized[code]=price(fee)
            count+=len(normalized)
            for code,fee in normalized.items():
                if fee is None:q.execute(f'DELETE FROM {table} WHERE user_id=ANY($1) AND {key}=$2',ids,code)
                else:q.execute(f'INSERT INTO {table}(user_id,{key},fee) SELECT id,$2,$3 FROM users WHERE id=ANY($1) ON CONFLICT(user_id,{key}) DO UPDATE SET fee=excluded.fee',ids,code,fee)
        if 'membership' in changes:
            membership=changes['membership']
            require(isinstance(membership,dict) and set(membership)=={'code','until'},'会员设置格式无效')
            code=membership['code'];until=parsedate(membership['until'])
            require(isinstance(code,str) and (code=='' or any(t['code']==code for t in config('tiers',q)['items'])),'会员等级不存在')
            if not code:until=None
            require(until is None or until>now(),'会员到期时间必须晚于现在')
            assign_membership(q,ids,code,until)
            count+=1
        require(count>0,'请选择至少一项要调整的设置')
        q.execute('UPDATE users SET updated_at=now() WHERE id=ANY($1)',ids)
        for id in ids:audit(q,request,'user.batch_settings',id,'批量调整账户：'+','.join(changes))
    return {'ok':True,'updated':len(ids)}


def account_options(q,request,id,value):
    if 'service_prices' in value:
        prices=value['service_prices']
        require(isinstance(prices,dict) and set(prices)<= {'registration_standard','registration_oauth','refund'},'服务专属价格格式无效')
        normalized={key:integer(fee,0,100000000,'专属价格须为有效的非负整数金额') for key,fee in prices.items()}
        q.execute('DELETE FROM user_service_prices WHERE user_id=$1',id)
        for service,fee in normalized.items():
            q.execute('INSERT INTO user_service_prices(user_id,service,fee) VALUES($1,$2,$3)',id,service,fee)
        audit(q,request,'user.service_pricing',id,'更新注册与代退专属价格：'+','.join(normalized))
    if 'membership_code' in value or 'membership_until' in value:
        old=q.one('SELECT membership_code,membership_until FROM users WHERE id=$1',id)
        code=value.get('membership_code',old['membership_code'])
        until=parsedate(value.get('membership_until',old['membership_until'].isoformat() if old['membership_until'] else None))
        require(isinstance(code,str) and (code=='' or any(t['code']==code for t in config('tiers',q)['items'])),'会员等级不存在')
        if not code:until=None
        require(until is None or until>now() or (code==old['membership_code'] and until==old['membership_until']),'会员到期时间必须晚于现在')
        assign_membership(q,[id],code,until)
    if 'product_prices' in value:
        prices=value['product_prices'];require(isinstance(prices,dict) and len(prices)<=200,'套餐专属价格格式无效')
        enabled={p['id'] for p in visible_products(q.all('SELECT id FROM products WHERE enabled'),config('payment',q).get('subscription_flow'))}
        normalized={}
        for product,fee in prices.items():
            require(isinstance(product,str) and internal_id(product) in enabled,'只能设置已开放套餐的价格')
            product=internal_id(product);require(product not in normalized,'套餐价格重复')
            normalized[product]=integer(fee,0,100000000,'套餐价格须为有效的非负整数金额')
        # Preserve prices hidden by the current flow, including historical Team premium.
        q.execute('DELETE FROM user_product_prices WHERE user_id=$1 AND product_id=ANY($2)',id,list(enabled))
        for product,fee in normalized.items():
            q.execute('INSERT INTO user_product_prices(user_id,product_id,fee) VALUES($1,$2,$3)',id,product,fee)
        audit(q,request,'user.product_pricing',id,'更新开放套餐专属价格：'+','.join(normalized))


@router.post('/users')
async def create_user(request: Request):
    v=await body(request);email=str(v.get('email','')).strip().lower();name=v.get('name','');pw=v.get('password','');role=v.get('role')
    require(email_valid(email) and 1<=length(name)<=80 and 10<=length(pw)<=72 and role in ('member','admin'),'请检查邮箱、称呼、角色和密码（至少 10 位）')
    id=uid();hashed=password_hash(pw)
    try:
        with db.transaction() as q:
            q.execute('SELECT pg_advisory_xact_lock(782518860)')
            q.execute('INSERT INTO users(id,email,name,password_hash,role,invite_code) VALUES($1,$2,$3,$4,$5,$6)',id,email,name,hashed,role,uid()[:10])
            if v.get('fee_override') is not None:
                q.execute('UPDATE users SET fee_override=$2 WHERE id=$1',id,integer(v['fee_override'],0,100000000))
            account_options(q,request,id,v)
            audit(q,request,'user.created',id,'新建账户 / '+role)
    except UniqueViolation:
        raise BusinessError('邮箱已存在或不可用')
    return {'id':id}


@router.patch('/users/{id}')
async def update_user(id: str,request: Request):
    v=await body(request);email=str(v.get('email','')).strip().lower();name=v.get('name','');role=v.get('role');status=v.get('status');fee=v.get('fee_override');pw=v.get('password','')
    require((email_valid(email) or (email=='admin' and db.scalar("SELECT email='admin' FROM users WHERE id=$1",id))) and 1<=length(name)<=80 and role in ('member','admin') and status in ('active','disabled'),'账户参数无效')
    if fee is not None:integer(fee,0,100000000,'账户参数无效')
    if pw:require(10<=length(pw)<=72,'密码须为 10～72 位')
    require(id!=current(request)['id'] or (role=='admin' and status=='active'),'不能停用或降级当前管理员')
    try:
        with db.transaction() as q:
            q.execute('SELECT pg_advisory_xact_lock(782518860)')
            q.execute('SELECT pg_advisory_xact_lock(1886001)')
            old=q.one('SELECT role,status FROM users WHERE id=$1 AND deleted_at IS NULL FOR UPDATE',id)
            require(old is not None,'用户不存在')
            if old['role']=='admin' and old['status']=='active' and (role!='admin' or status!='active'):
                require(q.scalar("SELECT count(*) FROM users WHERE role='admin' AND status='active' AND deleted_at IS NULL")>1,'至少保留一个可用管理员')
            q.execute('UPDATE users SET email=$2,name=$3,role=$4,status=$5,fee_override=$6,updated_at=now() WHERE id=$1',id,email,name,role,status,fee)
            account_options(q,request,id,v)
            if pw:q.execute('UPDATE users SET password_hash=$2 WHERE id=$1',id,password_hash(pw))
            if pw or status!='active' or role!=old['role']:
                q.execute('DELETE FROM sessions WHERE user_id=$1',id);q.execute('UPDATE api_keys SET revoked=true,secret_enc=NULL WHERE user_id=$1',id)
            audit(q,request,'user.updated',id,'资料、角色或单价修改')
    except UniqueViolation:
        raise BusinessError('邮箱已存在或修改失败')
    return {'ok':True}


@router.post('/users/pricing')
async def pricing(request: Request):
    v=await body(request);ids=v.get('ids',[]);fee=v.get('fee_override')
    require(isinstance(ids,list) and 1<=len(ids)<=500 and all(isinstance(x,str) for x in ids),'请选择账号并输入有效单价')
    if fee is not None:integer(fee,0,100000000)
    with db.transaction() as q:
        n=q.execute('UPDATE users SET fee_override=$2,updated_at=now() WHERE id=ANY($1) AND deleted_at IS NULL',sorted(ids),fee)
        audit(q,request,'user.pricing','batch',f'批量调整 {n} 个账户单价')
    return {'updated':n}


@router.post('/users/{id}/balance')
async def adjust_balance(id: str,request: Request):
    v=await body(request);amount=v.get('amount');reason=v.get('reason','');key=request.headers.get('Idempotency-Key','')
    integer(amount,-100000000000,100000000000)
    require(amount!=0 and 2<=length(reason)<=300 and 8<=len(key)<=120,'需要非零金额、调整原因和请求标识')
    rh=digest(json.dumps([id,amount,reason],ensure_ascii=False));lock=current(request)['id']+':'+key
    with db.transaction() as q:
        require(q.scalar('SELECT id FROM users WHERE id=$1 AND deleted_at IS NULL FOR UPDATE',id),'账户不存在',404)
        q.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',lock)
        prior=q.one('SELECT request_hash FROM mutation_keys WHERE key=$1',lock)
        if prior:
            require(prior['request_hash']==rh,'重复请求内容不一致',409)
            return {'ok':True,'replayed':True}
        credit(q,request,id,'adjustment','adjust:'+lock,reason,amount)
        audit(q,request,'balance.adjusted',id,f'调整 {amount} 分；{reason}')
        q.execute('INSERT INTO mutation_keys(key,actor_id,request_hash,result) VALUES($1,$2,$3,$4)',lock,current(request)['id'],rh,{'ok':True})
    return {'ok':True}


@router.get('/membership')
def members(summary_only:bool=False):
    rows=db.all('SELECT u.id,u.email,u.name,u.role,u.status,u.fee_override,u.membership_code,u.membership_until,u.membership_growth_credit,COALESCE(s.spent,0)::bigint AS spent FROM users u LEFT JOIN (SELECT user_id,sum(spent)::bigint AS spent FROM ('+SPEND+') s GROUP BY user_id) s ON s.user_id=u.id WHERE u.deleted_at IS NULL ORDER BY u.created_at DESC')
    distribution={};total=0;tiers=config('tiers');at=now();effective_tiers=membership_from({},0,tiers=tiers['items'],at=at)['tiers']
    for u in rows:
        m=membership_from(u,u['spent'],tiers=effective_tiers,at=at);u['membership']=m
        if u['role']=='member':
            code=m['current']['code'];distribution[code]=distribution.get(code,0)+1;total+=1
    products=visible_products(db.all('SELECT id,name FROM products WHERE enabled ORDER BY sort,id'),config('payment').get('subscription_flow'))
    return {'items':[] if summary_only else rows,'config':tiers,'distribution':distribution,'customers':total,
            'discount_services':discount_services(),'products':[{**p,'id':public_id(p['id'])} for p in products]}


@router.put('/users/{id}/membership')
async def set_membership(id: str,request: Request):
    v=await body(request);reason=str(v.get('reason','')).strip();code=v.get('code','');until=parsedate(v.get('until'))
    require(2<=length(reason)<=500,'请填写调整原因（2～500 字节）')
    require(until is None or until>now(),'到期时间必须晚于现在')
    with db.transaction() as q:
        q.execute('SELECT pg_advisory_xact_lock(782518860)')
        require(code=='' or any(t['code']==code for t in config('tiers',q)['items']),'等级不存在')
        if not code:until=None
        assign_membership(q,[id],code,until)
        audit(q,request,'membership.adjusted',id,json.dumps({'code':code,'until':until.isoformat() if until else None,'reason':reason},ensure_ascii=False))
    return {'ok':True}


@router.get('/topups')
def topups(status:str='',include_admin:bool=True,q:str='',page:int=1,page_size:int=10,sort_by:str='created_at',sort_dir:str='desc'):
    pagination(page,page_size);require(len(q)<=200,'搜索内容过长')
    require(status in ('','pending','approved','rejected','cancelled'),'充值状态无效')
    source=" FROM topups t JOIN users u ON u.id=t.user_id WHERE ($1='' OR t.status=$1) AND ($2::bool OR u.role<>'admin') AND ($3='' OR t.id ILIKE '%'||$3||'%' OR u.email ILIKE '%'||$3||'%' OR t.proof ILIKE '%'||$3||'%')"
    ordering=order_by(sort_by,sort_dir,{'email':'u.email','amount':'t.amount','method':'t.method','status':'t.status','created_at':'t.created_at','updated_at':'t.updated_at'},'created_at','t.id')
    rows=db.all('SELECT t.*,u.email'+source+' ORDER BY '+ordering+' LIMIT $4 OFFSET $5',status,include_admin,q,page_size,(page-1)*page_size)
    summary=db.one("SELECT count(*) AS total,count(*) FILTER(WHERE t.status='pending') AS pending"+source,status,include_admin,q)
    return list_result(rows,summary['total'],page,page_size,summary=summary)


@router.post('/topups/{id}/review')
async def review_topup(id: str,request: Request):
    v=await body(request);action=v.get('action');note=v.get('note','')
    require(action in ('approve','reject') and 2<=length(note)<=300,'请选择审核结果并填写审核依据')
    desired='approved' if action=='approve' else 'rejected'
    with db.transaction() as q:
        row=q.one('SELECT user_id,status,amount FROM topups WHERE id=$1 FOR UPDATE',id)
        require(row is not None,'申请不存在')
        if row['status']==desired:return {'ok':True,'replayed':True}
        require(row['status']=='pending','此申请已处理',409)
        if desired=='approved':credit(q,request,row['user_id'],'topup','topup:'+id,'人工核实充值到账',row['amount'])
        q.execute('UPDATE topups SET status=$2,note=$3,reviewed_by=$4,updated_at=now() WHERE id=$1',id,desired,note,current(request)['id'])
        audit(q,request,'topup.'+desired,id,note)
    return {'ok':True}


@router.get('/codes')
def codes(page: int=1,page_size: int=10,q: str='',status: str='',sort_by:str='created_at',sort_dir:str='desc'):
    return redeem_codes.listing(page,page_size,q,status,sort_by,sort_dir)


@router.get('/codes/usage')
def code_usage(period: str='day',date: str='',q: str='',page: int=1,page_size: int=50,sort_by:str='redeemed_at',sort_dir:str='desc'):
    return redeem_codes.usage(period,date,q,page,page_size,sort_by,sort_dir)


@router.post('/codes')
async def create_codes(request: Request):
    return await run_in_threadpool(redeem_codes.create,request,await body(request))


@router.post('/codes/batch')
async def batch_codes(request: Request):
    return await run_in_threadpool(redeem_codes.batch,request,await body(request))
