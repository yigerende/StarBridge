"""Shared contact details; only administrators may change them."""
import re
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from . import db
from .common import administrator, audit, body, config, member, require

router = APIRouter(prefix='/api/admin', dependencies=[Depends(administrator)])
customer_router = APIRouter(prefix='/api', dependencies=[Depends(member)])


@customer_router.get('/support')
def contacts():
    site=config('site')
    return {key:site.get(key,'') for key in ('support_url','telegram_group_url')}


def contact_url(value, *, group=False):
    require(isinstance(value,str) and len(value)<=500,'联系方式须为 500 字以内的账号或链接')
    value=value.strip()
    if not value:return ''
    if re.fullmatch(r'@[A-Za-z0-9_]{5,32}',value):value='https://t.me/'+value[1:]
    if value.startswith(('t.me/','telegram.me/')):value='https://'+value
    require(not re.search(r'[\s\\\x00-\x1f\x7f]',value),'联系方式中不能包含空白或控制字符')
    try:
        url=urlsplit(value)
        valid=url.scheme in ('http','https') and url.hostname and not url.username and not url.password
        if group:
            valid=valid and url.scheme=='https' and url.hostname in ('t.me','telegram.me') and url.port in (None,443) and bool(url.path.strip('/'))
    except ValueError:valid=False
    require(valid,'群组请填写 Telegram 群组链接或 @群组账号' if group else '客服请填写 @账号或有效的 http(s) 链接')
    return value


@router.patch('/support')
async def update(request:Request):
    value=await body(request)
    require(isinstance(value,dict) and set(value)=={'support_url','telegram_group_url'},'客服配置字段无效')
    value={'support_url':contact_url(value['support_url']), 'telegram_group_url':contact_url(value['telegram_group_url'],group=True)}
    return await run_in_threadpool(save,value,request)


def save(value,request):
    with db.transaction() as q:
        q.execute('SELECT pg_advisory_xact_lock(1886031)')
        q.execute("UPDATE settings SET value=value||$1::jsonb WHERE key='site'",value)
        audit(q,request,'settings.updated','support','更新客服与群组联系方式')
    return value
