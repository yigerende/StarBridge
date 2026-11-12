"""Same-origin transport for the independent registration application."""
import os
import json
import re
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from starlette.requests import ClientDisconnect

from .common import BusinessError, ip, require, authenticate
from starlette.concurrency import run_in_threadpool
from . import registration_sessions
from .common import response

router = APIRouter()
_client = None


def transport():
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(65, connect=5, pool=5),
            limits=httpx.Limits(max_connections=80, max_keepalive_connections=24),
            trust_env=False, follow_redirects=False)
    return _client


async def close_transport():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None

REQUEST_HEADERS = {'accept', 'content-type', 'cookie', 'authorization', 'origin',
                   'x-requested-with', 'idempotency-key', 'range', 'if-none-match'}
RESPONSE_HEADERS = {'content-type', 'content-disposition', 'content-length', 'content-encoding',
                    'cache-control', 'etag', 'last-modified', 'accept-ranges', 'content-range', 'retry-after'}


def authorize(request, path):
    from .rate_limits import enforce
    path='/'+path
    admin=path.startswith(('/api/admin/', '/api/v1/', '/admin/', '/admin-assets/')) or path=='/admin'
    if admin or path.startswith(('/api/user/', '/open/v1/')):
        authenticate(request,admin=admin)
        if request.method=='POST' and path.endswith('/registration-orders'):
            enforce(request,'order','order_per_minute')
        if path.endswith('/accounts/export') or path.endswith('/credentials'):
            enforce(request,'export','export_per_minute')


def enabled():
    return bool(os.getenv('REGISTRATION_URL') and len(os.getenv('REGISTRATION_GATEWAY_SECRET', '')) >= 32)


def destination(request):
    configured = os.getenv('REGISTRATION_URL', '').rstrip('/')
    base = urlsplit(configured)
    require(base.scheme in ('http', 'https') and base.hostname and not base.username
            and not base.password and base.path in ('', '/') and not base.query and not base.fragment,
            '注册服务地址未正确配置', 503)
    raw = request.scope['raw_path'].decode('ascii').removeprefix('/registration')
    path = unquote(raw)
    require(path.startswith('/') and not path.startswith('//') and '\\' not in path
            and not any(part in ('.', '..') for part in path.split('/'))
            and not any(ord(c) < 32 for c in path), '请求路径无效', 400)
    require(not any(part.startswith('.') for part in path.split('/') if part)
            and not path.startswith(('/src/','/services/','/backend/','/node_modules/')),
            '页面不存在', 404)
    query = request.scope.get('query_string', b'').decode('ascii')
    return configured + raw + ('?' + query if query else '')


@router.api_route('/registration', methods=['GET', 'HEAD'])
def registration_root():
    return Response(status_code=307, headers={'Location': '/registration/'})


@router.api_route('/registration/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
async def registration(request: Request, path: str):
    require(enabled(), '注册服务暂不可用，请稍后重试；订阅服务可正常使用', 503)
    target = destination(request)
    await run_in_threadpool(authorize,request,unquote(path))
    headers = {k: v for k, v in request.headers.items() if k.lower() in REQUEST_HEADERS}
    headers.update({'host': request.headers['host'], 'accept-encoding': 'identity',
                    'x-registration-gateway': os.environ['REGISTRATION_GATEWAY_SECRET'],
                    'x-registration-source-ip': ip(request)})
    client = transport()
    try:
        body = await request.body()
        account_path = re.fullmatch(r'api/(?:user|admin)/v1/accounts/(?:[^/]+/credentials|export)|open/v1/accounts/(?:[^/]+/credentials|export)', unquote(path))
        session_view = bool(account_path and request.method == 'GET' and path.endswith('/credentials'))
        session_download = False
        json_download = False
        if account_path and request.method == 'POST' and path.endswith('/export'):
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                payload = None
            if isinstance(payload, dict):
                format_name = payload.get('format', 'json' if '/admin/' in path else 'txt')
                session_download = format_name == 'email_session'
                json_download = format_name == 'json'
                if session_download:
                    # The original handler still validates ownership, scopes, expiry,
                    # selected IDs, feature switches and audit before any Session read.
                    body = json.dumps({**payload, 'format': 'json'}).encode()
        upstream = await client.send(client.build_request(request.method, target, headers=headers,
                                                          content=body), stream=True)
    except ClientDisconnect:
        return Response(status_code=499)
    except httpx.HTTPError:
        raise BusinessError('注册服务暂不可用，请稍后重试；订阅服务可正常使用', 503) from None
    except BaseException:
        raise
    if upstream.status_code == 200 and (session_view or session_download or json_download):
        try:
            raw = bytearray()
            async for part in upstream.aiter_bytes():
                raw.extend(part)
                require(len(raw) <= 64 * 1024 * 1024, '导出内容过大，请减少本次选择的账号数量', 413)
            result = json.loads(raw)
            values = [result] if session_view else json.loads(result['content'])
            values = await run_in_threadpool(registration_sessions.attach_sessions, values)
            if session_view:
                result = values[0]
            elif session_download:
                result = registration_sessions.session_export(values)
            else:
                result['content'] = json.dumps(values, ensure_ascii=False, indent=2)
            outgoing_response = response(result)
            outgoing_response.headers['Cache-Control'] = 'no-store'
            return outgoing_response
        finally:
            await upstream.aclose()
    if upstream.status_code==200 and request.method=='GET' and unquote(path)=='api/admin/v1/orders':
        try:
            from .order_stage import attach
            raw=bytearray()
            async for part in upstream.aiter_bytes():
                raw.extend(part)
                require(len(raw)<=8*1024*1024,'订单列表过大，请减少分页数量',413)
            result=json.loads(raw)
            await run_in_threadpool(attach,result['items'],'registration')
            outgoing_response=response(result)
            outgoing_response.headers['Cache-Control']='no-store'
            return outgoing_response
        finally:await upstream.aclose()
    outgoing = {k: v for k, v in upstream.headers.items() if k.lower() in RESPONSE_HEADERS}
    location = upstream.headers.get('location')
    if location:
        if location.startswith('/login'):
            outgoing['location'] = '/?business=registration'
        elif location.startswith('/') and not location.startswith('//'):
            outgoing['location'] = '/registration' + location
        else:
            await upstream.aclose()
            raise BusinessError('注册服务返回了无效跳转', 502)

    async def stream():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
    return StreamingResponse(stream(), status_code=upstream.status_code, headers=outgoing)
