'use strict';
const { digest, fail } = require('../src/platform/security');

async function identity(db, userId) {
  // Existing identities are a read-only projection of the unified account.
  // Page reads must not lock the wallet row or queue behind settlement.
  const existing=await db.one('SELECT * FROM registration_members WHERE xufu_user_id=$1', [userId]);
  if(existing){fail(existing.status==='active','登录已失效或账户已停用',401);return existing;}

  return db.tx(async q => {
    const u = (await q.query('SELECT * FROM users WHERE id=$1 AND deleted_at IS NULL FOR UPDATE', [userId])).rows[0];
    fail(u?.status === 'active', '登录已失效或账户已停用', 401);
    await q.query(`INSERT INTO platform_users(id,email,name,password_hash,xufu_user_id)
      VALUES($1,$2,$3,'unified-identity',$4) ON CONFLICT(xufu_user_id) DO NOTHING`,
      [`xufu:${u.id}`, `xufu-${u.id}@identity.invalid`, u.name, u.id]);
    if (u.invited_by && u.invited_by !== u.id) {
      await q.query(`INSERT INTO platform_users(id,email,name,password_hash,xufu_user_id)
        SELECT 'xufu:'||id,'xufu-'||id||'@identity.invalid',name,'unified-identity',id FROM users
        WHERE id=$1 ON CONFLICT(xufu_user_id) DO NOTHING`, [u.invited_by]);
      await q.query(`UPDATE platform_users p SET invited_by=i.id FROM platform_users i
        WHERE p.xufu_user_id=$1 AND i.xufu_user_id=$2 AND p.invited_by IS NULL`, [u.id, u.invited_by]);
    }
    return (await q.query('SELECT * FROM registration_members WHERE xufu_user_id=$1', [u.id])).rows[0];
  });
}

async function authenticate(db, request, api = false) {
  let userId, key;
  if (api) {
    const token = /^Bearer ([A-Za-z0-9_-]{20,160})$/.exec(request.headers.authorization || '')?.[1];
    fail(token, '请提供有效的 Bearer API Key', 401);
    key = await db.one('SELECT user_id,id FROM api_keys WHERE hash=$1 AND NOT revoked', [digest(token)]);
    if (key) await db.query('UPDATE api_keys SET last_used=now() WHERE id=$1', [key.id]);
    userId = key?.user_id;
    const flags = (await db.one("SELECT value FROM settings WHERE key='features'"))?.value;
    fail(flags?.keys !== false, '开发接入暂未开放', 403);
  } else {
    const token = String(request.headers.cookie || '').split(';').map(v => v.trim())
      .find(v => v.startsWith('xupay_session='))?.slice(14);
    fail(token, '请先登录星桥', 401);
    userId = (await db.one('SELECT user_id FROM sessions WHERE hash=$1 AND expires_at>now()', [digest(token)]))?.user_id;
  }
  fail(userId, '登录已失效，请重新登录星桥', 401);
  const user = await identity(db, userId);
  if (key) Object.assign(user, { key_id: key.id, ...(key.scopes ? { scopes: key.scopes } : {}) });
  request.user = user;
  return user;
}
module.exports = { authenticate, identity };
