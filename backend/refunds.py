"""Native StarBridge refund API; the private HUIXU core shares our users and ledger."""
import asyncio
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import httpx
from fastapi import APIRouter, Depends, Query, Request
from . import db, membership
from .common import administrator, audit, body, current, member, require, integer

router=APIRouter(prefix='/api',dependencies=[Depends(member)])
_process=None
_client=None
_token=None
_monitor=None
_stopping=False

async def start():
    global _monitor,_stopping
    _stopping=False
    await launch()
    _monitor=asyncio.create_task(supervise(),name='refund-supervisor')

async def supervise():
    while not _stopping:
        await _process.wait()
        if _stopping:return
        logging.getLogger('starbridge').error('Refund execution module exited; restoring persisted work')
        await close_process()
        await asyncio.sleep(1)
        while not _stopping:
            try:
                await launch()
                break
            except Exception:
                logging.getLogger('starbridge').error('Refund execution module restart will retry')
                await asyncio.sleep(3)

async def launch():
    global _process,_client,_token
    binary=Path(os.getenv('REFUND_CORE_BINARY',str(db.ROOT/'bin'/('refund-core.exe' if os.name=='nt' else 'refund-core'))))
    require(binary.is_file(),'代退模块尚未构建，请先执行 scripts/build_refund.py',503)
    _token=secrets.token_urlsafe(48)
    env={**os.environ,'REFUND_INTERNAL_TOKEN':_token,'REFUND_PYTHON':sys.executable,
         'REFUND_RUNNER':str(db.ROOT/'services/refund-core/runtime/refund_runner.py')}
    _process=await asyncio.create_subprocess_exec(str(binary),env=env,stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,stderr=None,
        **({'creationflags':0x08000000} if os.name=='nt' else {}))
    try:
        line=await asyncio.wait_for(_process.stdout.readline(),15)
        port=json.loads(line)['port']
        require(isinstance(port,int) and 0<port<65536,'代退模块启动失败',503)
        _client=httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=70,
            limits=httpx.Limits(max_connections=24,max_keepalive_connections=12),headers={'Authorization':'Bearer '+_token})
        await call('health')
    except BaseException:
        await close_process()
        raise

async def stop():
    global _monitor,_stopping
    _stopping=True
    if _monitor:
        _monitor.cancel()
        await asyncio.gather(_monitor,return_exceptions=True)
        _monitor=None
    await close_process()

def healthy():
    return _monitor is not None and not _monitor.done() and _process is not None and _process.returncode is None and _client is not None

async def close_process():
    global _process,_client
    if _client:
        await _client.aclose();_client=None
    if _process:
        if _process.returncode is None:
            _process.stdin.close()
            try:await asyncio.wait_for(_process.wait(),20)
            except asyncio.TimeoutError:_process.kill();await _process.wait()
        _process=None

async def call(action,actor='',**value):
    require(_client is not None and _process is not None and _process.returncode is None,'代退模块暂不可用，请联系管理员',503)
    try:
        result=await _client.post('/rpc',json={'action':action,'actor':actor,**value})
        data=result.json()
    except (httpx.HTTPError,ValueError):
        require(False,'代退响应暂未确认，请使用原请求标识查询或重试',503)
    require(result.status_code==200,data.get('error','代退操作失败'),result.status_code)
    return data

def pricing(user):
    cfg=db.one('SELECT accepting,service_fee,enabled FROM refund_settings WHERE id')
    m=membership.membership_for(user['id'])
    fee=membership.fee_for(m,cfg['service_fee'],'refund')
    return {'standard':cfg['service_fee'],'fee':fee,'saved':max(0,cfg['service_fee']-fee),
            'membership':m,'price_source':membership.fee_source(m,'refund'),'accepting':cfg['accepting'],'executing':cfg['enabled'],'unit':'cent_R'}

@router.get('/refund/quote')
@router.get('/v1/refund/quote')
def quote(request:Request):return pricing(current(request))

@router.post('/refund/orders')
@router.post('/v1/refund/orders')
async def create(request:Request):
    v=await body(request)
    require(isinstance(v,dict) and v.get('authorized') is True,'请确认账号授权及服务说明')
    public=request.url.path.startswith('/api/v1/')
    require(set(v)<= ({'accounts','authorized'} if public else {'accounts','authorized','idempotency_key'}),'请求包含不支持的字段')
    accounts=v.get('accounts')
    require(isinstance(accounts,list) and 1<=len(accounts)<=500,'每批须包含 1～500 个账号')
    require(all(isinstance(a,dict) and set(a)=={'email','password','totp_secret'} and all(isinstance(x,str) for x in a.values()) for a in accounts),'请填写邮箱、密码和 2FA 密钥')
    key=request.headers.get('Idempotency-Key') or ('' if public else v.get('idempotency_key',''))
    require(isinstance(key,str) and 8<=len(key)<=128,'请提供 8～128 位 Idempotency-Key')
    user=current(request)
    fee=await asyncio.to_thread(pricing,user)
    return await call('create',user['id'],key=key,accounts=accounts,fee=fee['fee'])

@router.get('/refund/orders')
@router.get('/v1/refund/orders')
async def listing(request:Request,q:str='',status:str='',page:int=Query(1,ge=1,le=100000),page_size:int=Query(50,ge=1,le=200)):
    require(status in ('','active','queued','processing','verifying','attention','succeeded','failed','banned','cancelled'),'状态无效')
    return await call('list',current(request)['id'],query=q,status=status,page=page,size=page_size)

@router.get('/refund/orders/{id}')
@router.get('/v1/refund/orders/{id}')
async def detail(id:str,request:Request):return await call('detail',current(request)['id'],id=id)

@router.post('/refund/orders/{id}/cancel')
@router.post('/v1/refund/orders/{id}/cancel')
async def cancel(id:str,request:Request):return await call('cancel',current(request)['id'],id=id)

admin=APIRouter(prefix='/api/admin/refund',dependencies=[Depends(administrator)])

@admin.get('/settings')
async def settings(request:Request):
    data=await call('settings',current(request)['id'])
    data.update(await asyncio.to_thread(db.one,'SELECT accepting,service_fee FROM refund_settings WHERE id'))
    return data

@admin.put('/settings')
async def save_settings(request:Request):
    v=await body(request)
    require(isinstance(v,dict) and isinstance(v.get('settings'),dict),'设置格式错误')
    result=await call('save_settings',current(request)['id'],settings=v['settings'])
    return result

@admin.put('/pricing')
async def save_pricing(request:Request):
    v=await body(request);integer(v.get('service_fee'),0,10000000,'服务费超出范围')
    require(isinstance(v.get('accepting'),bool),'请设置是否开放办理')
    def save():
        with db.transaction() as q:
            q.execute('UPDATE refund_settings SET service_fee=$1,accepting=$2,updated_at=now() WHERE id',v['service_fee'],v['accepting'])
            audit(q,request,'refund.pricing','refund','更新代退服务费和办理开关')
    await asyncio.to_thread(save)
    return {'ok':True}

@admin.get('/orders')
async def monitor(request:Request,q:str='',status:str='',fee_status:str='',focus:str='',sort:str='priority',start:str='',end:str='',page:int=Query(1,ge=1,le=100000),page_size:int=Query(50,ge=1,le=200)):
    return await call('monitor',current(request)['id'],query=q,status=status,page=page,size=page_size,
                      fee_status=fee_status,focus=focus,sort=sort,start=start,end=end)

@admin.get('/orders/{id}/timeline')
async def timeline(id:str,request:Request):return await call('timeline',current(request)['id'],id=id)

@admin.get('/orders/{id}')
async def admin_detail(id:str,request:Request):
    return await call('admin_detail',current(request)['id'],id=id)

@admin.post('/orders/{id}/resume')
async def resume(id:str,request:Request):
    v=await body(request)
    require(v.get('action') in ('resume','verify_only','confirm_accepted','confirm_rejected','fail_cleaned'),'操作无效')
    require(isinstance(v.get('note'),str) and 5<=len(v['note'])<=1000,'请填写核实依据或操作说明（5～1000 字）')
    return await call('resume',current(request)['id'],id=id,resume=v['action'],note=v['note'])

@admin.post('/mothers/preview')
async def preview(request:Request):
    v=await body(request)
    require(isinstance(v.get('session'),str) and len(v['session'])<=100000,'Session 格式错误')
    return await call('preview',current(request)['id'],session=v['session'])

@admin.post('/{resource}')
@admin.post('/{resource}/check')
async def resource_save(resource:str,request:Request):
    require(resource in ('mothers','proxies'),'资源不存在',404)
    value=await body(request);require(isinstance(value,dict),'资源格式错误')
    kind='mother' if resource=='mothers' else 'proxy'
    action=('probe_' if request.url.path.endswith('/check') else 'save_')+kind
    return await call(action,current(request)['id'],**{kind:value})

@admin.delete('/{resource}/{id}')
async def resource_delete(resource:str,id:str,request:Request):
    require(resource in ('mothers','proxies'),'资源不存在',404)
    return await call('delete_'+('mother' if resource=='mothers' else 'proxy'),current(request)['id'],id=id)
