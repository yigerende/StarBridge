"""Same-origin transport for the independent registration application."""
import os
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from starlette.requests import ClientDisconnect

from .common import BusinessError, ip, require, authenticate
from starlette.concurrency import run_in_threadpool

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
        upstream = await client.send(client.build_request(request.method, target, headers=headers,
                                                          content=await request.body()), stream=True)
    except ClientDisconnect:
        return Response(status_code=499)
    except httpx.HTTPError:
        raise BusinessError('注册服务暂不可用，请稍后重试；订阅服务可正常使用', 503) from None
    except BaseException:
        raise
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
