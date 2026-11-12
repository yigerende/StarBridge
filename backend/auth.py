import secrets
from fastapi import APIRouter, Depends, Request
from . import db
from .common import *
from .email_auth import security_config, email_allowed, consume
from .auth_runtime import run_auth
from .request_source import cookie_secure

router = APIRouter(prefix='/api')


def insert_session(q, user_id, hours):
    token = secrets.token_urlsafe(32)
    q.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+($3*interval '1 hour'))",digest(token),user_id,hours)
    return token


def session_response(request, token, hours):
    result = response({'ok':True})
    result.set_cookie('xupay_session',token,max_age=hours*3600,httponly=True,secure=cookie_secure(request),samesite='strict')
    return result


def create_session(request, user_id, verified_hash, verified_email, verified_name=None):
    hours = config('risk').get('session_hours',24)
    with db.transaction() as q:
        user=q.one('SELECT email,name,password_hash,status FROM users WHERE id=$1 AND deleted_at IS NULL FOR UPDATE',user_id)
        require(user and user['status']=='active' and user['password_hash']==verified_hash and user['email']==verified_email,'登录信息已变更，请重新登录',401)
        if verified_name is not None:
            matches=q.all('SELECT id FROM users WHERE lower(btrim(name))=$1 AND deleted_at IS NULL LIMIT 2',verified_name)
            require(len(matches)==1 and matches[0]['id']==user_id and user['name'].strip().lower()==verified_name,'登录信息已变更，请使用邮箱重新登录',401)
        token=insert_session(q,user_id,hours)
    return session_response(request,token,hours)


@router.post('/auth/login')
async def login(request: Request):
    return await run_auth(login_sync,request,await body(request))


def login_sync(request, v):
    from . import rate_limits
    require(isinstance(v.get('password'),str) and length(v['password'])<=72,'账号或密码不正确',401)
    identifier=str(v.get('email','')).strip().lower()
    require(1<=length(identifier)<=254,'请填写用户名或邮箱',401)
    user = db.one('SELECT id,email,password_hash,status,role FROM users WHERE email=$1 AND deleted_at IS NULL',identifier)
    verified_name=None
    if user is None:
        matches=db.all('SELECT id,email,password_hash,status,role FROM users WHERE lower(btrim(name))=$1 AND deleted_at IS NULL LIMIT 2',identifier)
        if len(matches)==1:
            user=matches[0];verified_name=identifier
    dummy = '$2b$12$vuCQLWceDWoxSWQG6TLbqep2q5iYtXvwi5ok8gQ9MluCb8FH13H96'
    valid = None
    if user and user['role']=='admin' and user['status']=='active':
        valid=password_matches(v['password'],user['password_hash'])
        if valid:request.state.verified_identity=user
    if not rate_limits.administrator(request):
        require(not db.scalar('SELECT ip FROM blocked_ips WHERE ip=$1 AND expires_at>now()',ip(request)),'当前来源暂不可访问',403)
    rate_limits.enforce(request,'edge','edge_per_minute')
    rate_limits.enforce(request,'login','login_per_minute')
    from .login_slider import consume as consume_slider
    consume_slider(request,v.get('slider_proof'))
    if valid is None:valid=password_matches(v['password'],user['password_hash'] if user else dummy)
    if not user or not valid or user['status'] != 'active':
        event(request,'login_failed','medium','账号或密码校验失败')
        raise BusinessError('账号或密码不正确，或账户已停用；使用称呼无法登录时请改用邮箱',401)
    return create_session(request,user['id'],user['password_hash'],user['email'],verified_name)


@router.post('/auth/logout')
def logout(request: Request):
    token = request.cookies.get('xupay_session','')
    if token: db.execute('DELETE FROM sessions WHERE hash=$1',digest(token))
    result = response({'ok':True})
    result.delete_cookie('xupay_session',httponly=True,secure=cookie_secure(request),samesite='strict')
    return result


@router.post('/auth/register')
async def register(request: Request):
    return await run_auth(register_sync,request,await body(request))


def register_sync(request, v):
    cfg = security_config()
    require(cfg['registration_open'],'暂未开放注册',403)
    from .rate_limits import enforce
    enforce(request,'register','register_per_minute')
    email = str(v.get('email','')).strip().lower(); name=v.get('name',''); pw=v.get('password','')
    require(email_valid(email) and isinstance(name,str) and 1 <= length(name.strip()) <= 80 and 10 <= length(pw) <= 72,'请填写有效邮箱、用户名和 10～72 位密码')
    require(email_allowed(email,cfg), '此邮箱域名暂不支持注册')
    inviter = None
    if v.get('invite'):
        inviter = db.scalar('SELECT id FROM users WHERE invite_code=$1 AND deleted_at IS NULL',v['invite'])
        require(inviter is not None,'邀请标识无效')
    user_id=uid()
    from psycopg2.errors import UniqueViolation
    error = ''
    hashed = password_hash(pw)
    hours = config('risk').get('session_hours',24)
    try:
        with db.transaction() as q:
            q.execute('SELECT pg_advisory_xact_lock(1886031)')
            cfg = security_config(q)
            require(cfg['registration_open'], '暂未开放注册', 403)
            require(email_allowed(email,cfg), '此邮箱域名暂不支持注册')
            if cfg.get('email_verify_enabled'):
                error = consume(q,email,'register',v.get('code'))
            if not error:
                q.execute('INSERT INTO users(id,email,name,password_hash,invite_code,invited_by) VALUES($1,$2,$3,$4,$5,$6)',user_id,email,name.strip(),hashed,uid()[:10],inviter)
                token=insert_session(q,user_id,hours)
    except UniqueViolation:
        raise BusinessError('该邮箱已注册或暂不可用',409)
    require(not error, error)
    return session_response(request,token,hours)


@router.post('/auth/reset-password')
async def reset_password(request: Request):
    return await run_auth(reset_password_sync,request,await body(request))


def reset_password_sync(request, v):
    from .rate_limits import enforce
    enforce(request,'password-reset','reset_per_minute')
    email=str(v.get('email','')).strip().lower();pw=v.get('password','')
    require(email_valid(email) and 10 <= length(pw) <= 72, '请填写有效邮箱和 10～72 位新密码')
    hashed=password_hash(pw)
    with db.transaction() as q:
        user=q.one('SELECT id,status,password_hash FROM users WHERE email=$1 AND deleted_at IS NULL FOR UPDATE',email)
        error=consume(q,email,'reset',v.get('code'),user)
        if not error:
            q.execute('UPDATE users SET password_hash=$2,updated_at=now() WHERE id=$1',user['id'],hashed)
            q.execute('DELETE FROM sessions WHERE user_id=$1',user['id'])
            q.execute('UPDATE api_keys SET revoked=true,secret_enc=NULL WHERE user_id=$1',user['id'])
            q.execute("INSERT INTO audit_events(actor_id,action,target,detail,ip) VALUES($1,'password.reset',$1,'通过邮箱重置密码并撤销登录会话和 API 密钥',$2)",user['id'],ip(request))
    require(not error,error)
    return {'ok':True,'message':'密码已重置，请使用新密码登录。原登录会话和 API 密钥已失效。'}


@router.patch('/me',dependencies=[Depends(member)])
async def profile(request: Request):
    return await run_auth(profile_sync,request,await body(request))


def profile_sync(request, v):
    name=v.get('name',''); pw=v.get('password','')
    require(1 <= length(name) <= 80,'称呼长度须为 1～80')
    user_id=current(request)['id']
    with db.transaction() as q:
        user=q.one('SELECT password_hash,status,deleted_at FROM users WHERE id=$1 FOR UPDATE',user_id)
        require(user and user['status']=='active' and user['deleted_at'] is None,'登录已失效或账户已停用',401)
        hashed=user['password_hash']
        if pw:
            require(10 <= length(pw) <= 72 and password_matches(v.get('old_password',''),hashed),'原密码不正确或新密码不足 10 位')
            hashed=password_hash(pw)
            q.execute('DELETE FROM sessions WHERE user_id=$1',user_id)
        q.execute('UPDATE users SET name=$2,password_hash=$3,updated_at=now() WHERE id=$1',user_id,name,hashed)
        audit(q,request,'profile.updated',user_id,'更新个人资料')
    return {'ok':True,'relogin':bool(pw)}
