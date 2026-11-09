"""Unified account projections; registration execution remains in GPT Register."""
from . import db
from .common import current, digest, require, now, limit


def installed():
    return bool(db.scalar("SELECT to_regclass('registration_migrations')"))


def legacy_keys(user_id):
    if not installed():return []
    return db.all("""SELECT 'registration:'||k.id AS id,k.name||'（历史注册专用）' AS name,
      k.prefix,(k.revoked OR k.xufu_credential_hash<>md5(u.password_hash)) AS revoked,NULL::timestamptz AS last_used,k.created_at,k.scopes
      FROM platform_api_keys k JOIN platform_users p ON p.id=k.user_id JOIN users u ON u.id=p.xufu_user_id
      WHERE p.xufu_user_id=$1 ORDER BY k.created_at DESC""",user_id)


def revoke_key(user_id, key_id):
    require(installed(),'密钥不存在',404)
    return db.execute('UPDATE platform_api_keys k SET revoked=true FROM platform_users p WHERE p.id=k.user_id AND p.xufu_user_id=$1 AND k.id=$2',user_id,key_id)


def redeem(request, code):
    from .redeem_codes import transaction
    user_id=current(request)['id']
    require(installed(),'兑换码无效')
    require(isinstance(code,str) and 20<=len(code.strip())<=100,'兑换码无效')
    require(limit('registration-redeem:'+user_id,10),'兑换过于频繁',429)
    with transaction() as q:
        user=q.one('SELECT id,status,deleted_at,name FROM users WHERE id=$1 FOR UPDATE',user_id)
        require(user and user['status']=='active' and user['deleted_at'] is None,'账户已停用或删除',403)
        q.execute("""INSERT INTO platform_users(id,email,name,password_hash,xufu_user_id)
          VALUES($1,$2,$3,'unified-identity',$4) ON CONFLICT(xufu_user_id) DO NOTHING""",
          'xufu:'+user_id,'xufu-'+user_id+'@identity.invalid',user['name'],user_id)
        member=q.scalar('SELECT id FROM platform_users WHERE xufu_user_id=$1',user_id)
        row=q.one('SELECT * FROM platform_redeem_codes WHERE hash=$1 FOR UPDATE',digest(code.strip()))
        require(row and not row['disabled'] and not row['redeemed_by']
                and (row['expires_at'] is None or row['expires_at']>now()),'兑换码无效、停用、过期或已使用')
        q.scalar("SELECT registration_post_ledger($1,$2,'redeem',$3,0,'注册兑换码入账')",member,'redeem:'+row['id'],row['points'])
        q.execute('UPDATE platform_redeem_codes SET redeemed_by=$2,redeemed_at=now() WHERE id=$1',row['id'],member)
    return {'ok':True,'amount':row['points']*100}


def summary(user_id=None):
    if not installed():return {'orders':0,'pending':0,'delivered':0,'spent':0}
    return db.one("""SELECT count(*) AS orders,count(*) FILTER(WHERE o.status IN ('queued','running','review')) AS pending,
      coalesce(sum(o.delivered),0) AS delivered,coalesce(sum((o.charged*100)::bigint),0) AS spent
      FROM platform_orders o JOIN platform_users p ON p.id=o.user_id WHERE ($1::text IS NULL OR p.xufu_user_id=$1)""",user_id)


def legacy_ledger(user_id):
    if not installed():return []
    return db.all("""SELECT 'registration-history:'||l.id AS id,
      CASE l.kind WHEN 'reserve' THEN 'freeze' WHEN 'release' THEN 'unfreeze' WHEN 'adjust' THEN 'adjustment' ELSE l.kind END AS kind,
      ((l.points_delta+l.frozen_delta)*100)::bigint AS amount,(l.points_after+l.frozen_after)*100 AS balance_after,
      l.frozen_after*100 AS frozen_after,l.note AS description,l.created_at,'registration' AS business,
      CASE WHEN l.kind='reserve' THEN substring(l.reference FROM 9) ELSE i.order_id END AS registration_order_id,
      (l.points_delta*100)::bigint AS available_delta,(l.frozen_delta*100)::bigint AS frozen_delta,
      NULL::jsonb AS related_order,'{}'::jsonb AS payment,true AS legacy_registration
      FROM platform_ledger l JOIN platform_users p ON p.id=l.user_id
      LEFT JOIN platform_items i ON l.kind IN ('charge','release') AND i.id=split_part(l.reference,':',2)
      WHERE p.xufu_user_id=$1 AND l.unified_entry_id IS NULL ORDER BY l.created_at DESC,l.id DESC LIMIT 300""",user_id)


def legacy_spend(user_id):
    if not installed():return 0
    return db.scalar("""SELECT coalesce(sum(-(l.frozen_delta*100)::bigint),0) FROM platform_ledger l
      JOIN platform_users p ON p.id=l.user_id WHERE p.xufu_user_id=$1 AND l.kind='charge' AND l.unified_entry_id IS NULL""",user_id)


def reward_source():
    subscription="SELECT inviter_id,invitee_id,rate_percent,fee,granted,entitled,recovered,created_at FROM invitation_rewards"
    if not installed():return '('+subscription+')'
    registration="""SELECT i.xufu_user_id AS inviter_id,p.xufu_user_id AS invitee_id,r.rate_percent,(r.fee*100)::bigint AS fee,
      r.amount::bigint*100 AS granted,r.amount::bigint*100 AS entitled,0::bigint AS recovered,r.created_at
      FROM platform_partner_rewards r JOIN platform_users p ON p.id=r.invitee_id
      JOIN platform_users i ON i.id=r.inviter_id"""
    return '('+subscription+' UNION ALL '+registration+')'
