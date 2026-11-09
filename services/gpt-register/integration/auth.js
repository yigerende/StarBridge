'use strict';
const { authenticate } = require('./identity');
const { userView } = require('../src/platform/auth');
const { publicBrand } = require('../src/platform/branding');
const { activeAnnouncements, customerNavigation } = require('../src/platform/site-controls');
const { digest, fail } = require('../src/platform/security');

function registerAuth(app, db) {
  app.get('/api/public', async () => {
    const s = await db.settings();
    return { site_name: s.site_name, brand: publicBrand(s.brand), announcement: s.announcement,
      announcements: activeAnnouncements(s), features: s.features, registration_open: false,
      login_slider_enabled: false, email_verify_enabled: false, email_domains: [],
      password_reset_available: false, orders_open: s.orders_open, price_standard: s.price_standard,
      price_oauth: s.price_oauth, max_quantity: s.max_quantity, unified_account: true };
  });
  app.get('/api/user/v1/me', async r => ({ ...userView(r.user),
    navigation: customerNavigation(await db.settings(), r.user.role), unified_account: true }));
  app.post('/api/auth/logout', async (r, reply) => {
    const token = String(r.headers.cookie || '').split(';').map(s => s.trim())
      .find(s => s.startsWith('xupay_session='))?.slice(14);
    if (token) await db.query('DELETE FROM sessions WHERE hash=$1', [digest(token)]);
    return reply.send({ ok: true });
  });
  for (const route of ['login', 'register', 'email-code', 'reset-password'])
    app.post('/api/auth/' + route, async () => fail(false, '请通过星桥统一账户入口操作', 409));
  app.patch('/api/user/v1/me', async () => fail(false, '请通过星桥个人设置修改资料', 409));
}
module.exports = { authenticate, registerAuth };
