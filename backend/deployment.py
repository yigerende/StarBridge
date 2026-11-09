"""Admin-only requests to the dedicated, host-side StarBridge updater."""
import json
import os
import re
import time
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, Request
from . import db
from .common import administrator, audit, body, require
from .request_origin import parse_origin

router = APIRouter(prefix='/api/admin/system', dependencies=[Depends(administrator)])
BUSY = {'checking', 'fetching', 'building', 'waiting', 'backing_up', 'switching', 'verifying', 'rolling_back', 'recovery_required'}


def directory():
    return Path(os.getenv('MAINTENANCE_DIR', '/run/starbridge-maintenance'))


def read_json(name, default=None):
    try:
        return json.loads((directory() / name).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def origin_value(value):
    require(isinstance(value, str) and len(value) <= 300, '请输入完整的 http:// 或 https:// 访问地址')
    value = value.strip()
    parsed = parse_origin(value)
    require(parsed and re.fullmatch(r'[A-Za-z0-9.:[\]-]+', parsed[1]), '地址只能包含协议、域名或 IP、可选端口，不要填写路径、参数或账号')
    scheme, host, port = parsed
    host = '[' + host + ']' if ':' in host else host
    return f'{scheme}://{host}' + (f':{port}' if port != (443 if scheme == 'https' else 80) else '')


def maintenance_active():
    status = read_json('status.json', {})
    return status.get('phase') in {'waiting', 'backing_up', 'switching', 'verifying', 'rolling_back', 'recovery_required'}


@router.get('/update')
def status():
    data = read_json('status.json', {})
    heartbeat = read_json('heartbeat.json', {})
    available = time.time() - heartbeat.get('time', 0) < 20
    return {**data, 'available': available, 'pending': (directory() / 'requests/request.json').exists(),
            'public_origin': os.getenv('PUBLIC_ORIGIN', ''),
            'current_version': os.getenv('APP_VERSION', '') or data.get('current_version', 'local'),
            'repository_url': 'https://github.com/yigerende/StarBridge'}


@router.post('/update/{action}')
async def submit(action: str, request: Request):
    require(action in {'check', 'prepare', 'restart', 'rollback'}, '未知更新操作', 404)
    value = await body(request)
    require(isinstance(value, dict), '参数无效')
    state = status()
    require(state['available'], '更新服务暂不可用，请稍后重试', 503)
    require(not state['pending'] and state.get('phase') not in BUSY, '已有更新操作正在进行', 409)
    job = {'id': uuid.uuid4().hex, 'action': action, 'created_at': time.time()}
    if action == 'prepare':
        job['public_origin'] = origin_value(value.get('public_origin', state['public_origin']))
    elif action == 'restart':
        require(state.get('phase') == 'ready' and value.get('prepared_id') == state.get('prepared_id'), '请先完成更新准备', 409)
        job['prepared_id'] = state['prepared_id']
    elif action == 'rollback':
        candidates = [v for v in state.get('rollback_versions', []) if v.get('compatible')]
        require(any(v['id'] == value.get('version_id') for v in candidates), '该版本当前不可回退', 409)
        job['version_id'] = value['version_id']
    pending = directory() / 'requests/request.json'
    temporary = pending.with_name(job['id'] + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as f:
            json.dump(job, f)
            f.flush()
            os.fsync(f.fileno())
        with db.transaction() as q:
            audit(q, request, 'system.' + action, job['id'], '准备版本更新' if action == 'prepare' else '系统维护操作')
        try:
            os.link(temporary, pending)  # atomic publication; never overwrite another request
        except FileExistsError:
            require(False, '已有更新操作正在进行', 409)
    except PermissionError:
        require(False, '更新服务目录不可写，请检查部署配置', 503)
    finally:
        temporary.unlink(missing_ok=True)
    return {'ok': True, 'operation_id': job['id']}
