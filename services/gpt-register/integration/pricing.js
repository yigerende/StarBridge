'use strict';
const { fail } = require('../src/platform/security');
const { lockMember } = require('./member-lock');

// Match backend/membership.py: settled spend and active growth credit select one tier.
async function prices(q, memberId, settings) {
  const user = (await q.query(`SELECT u.id,u.membership_code,u.membership_until,u.membership_growth_credit,
    coalesce((SELECT sum(s.spent) FROM (
      SELECT l.order_id,greatest(0,-sum(l.amount)) AS spent
      FROM ledger l JOIN orders o ON o.id=l.order_id AND o.user_id=l.user_id
      WHERE l.user_id=u.id AND o.status='succeeded'
        AND ((l.kind='charge' AND l.amount<0) OR (l.kind='refund' AND l.amount>0))
      GROUP BY l.order_id) s),0) AS spent
    FROM users u JOIN platform_users p ON p.xufu_user_id=u.id WHERE p.id=$1`, [memberId])).rows[0];
  fail(user, '用户不存在', 404);
  const exclusive = Object.fromEntries((await q.query(
    "SELECT service,fee FROM user_service_prices WHERE user_id=$1 AND service IN ('registration_standard','registration_oauth')", [user.id]
  )).rows.map(row => [row.service, Number(row.fee)]));
  const config = (await q.query("SELECT value FROM settings WHERE key='tiers'")).rows[0]?.value;
  const tiers = (config?.items || [{ code: 'L1', name: '启程会员', threshold: 0, discount: 100 }])
    .slice().sort((a,b) => a.threshold-b.threshold).map((t,i) => ({...t,code:t.code || `L${i+1}`}));
  fail(tiers.length > 0, '会员价格配置不可用', 503);
  const active = !!user.membership_code && (!user.membership_until || new Date(user.membership_until) > new Date());
  const spent = Number(user.spent) + (active ? Number(user.membership_growth_credit) : 0);
  let current = tiers[0], source = active ? 'manual' : 'automatic', until = active ? user.membership_until : null;
  for (const tier of tiers) if (spent >= tier.threshold) current = tier;
  fail(Number.isInteger(current.discount) && current.discount >= 0 && current.discount <= 100, '会员价格配置不可用', 503);
  const membership = { current, source, until };
  const benefitRules = (await q.query("SELECT value FROM settings WHERE key='membership_pricing'")).rows[0]?.value || {};
  return {
    membership,
    products: [false,true].map(oauth => {
      const standard = Math.round(Number(oauth ? settings.price_oauth : settings.price_standard)*100);
      const own = exclusive[oauth ? 'registration_oauth' : 'registration_standard'];
      const eligible = benefitRules[oauth ? 'registration_oauth' : 'registration_standard'] ?? !oauth;
      const fee = own == null ? (eligible ? Math.floor((standard*current.discount+50)/100) : standard) : own;
      fail(Number.isSafeInteger(standard) && standard>=0 && Number.isSafeInteger(fee) && fee>=0, '服务费配置不可用', 503);
      return { name: oauth ? 'OAuth 账号' : '标准账号', need_oauth: oauth,
        standard_price: standard/100, unit_price: fee/100, saved: (standard-fee)/100,
        membership_eligible: eligible, price_source: own == null ? (eligible ? 'membership' : 'standard') : 'account_override' };
    }),
  };
}

async function catalog(db, memberId) {
  return db.tx(async q => {
    await lockMember(q, memberId, 'id', true);
    const settings = (await q.query('SELECT value FROM platform_settings WHERE id=1 FOR SHARE')).rows[0].value;
    return { orders_open: settings.orders_open, max_quantity: settings.max_quantity, currency: 'points',
      ...await prices(q, memberId, settings) };
  });
}

module.exports = { prices, catalog };
