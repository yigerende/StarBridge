'use strict';
async function lockMember(q, id, fields = '*', share = false) {
  if (!['*', 'id', 'id,status', 'role,status'].includes(fields)) throw new Error('Invalid member projection');
  if (process.env.XUFU_INTEGRATION !== '1')
    return q.query(`SELECT ${fields} FROM registration_members WHERE id=$1 FOR ${share ? 'SHARE' : 'UPDATE'}`, [id]);
  await q.query(`SELECT u.id FROM users u JOIN platform_users p ON p.xufu_user_id=u.id
    WHERE p.id=$1 FOR ${share ? 'SHARE' : 'UPDATE'} OF u`, [id]);
  return q.query(`SELECT ${fields} FROM registration_members WHERE id=$1`, [id]);
}
module.exports = { lockMember };
