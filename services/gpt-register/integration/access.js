'use strict';
const crypto = require('node:crypto');
const net = require('node:net');
const { fail } = require('../src/platform/security');

function registerAccess(app) {
  const secret = process.env.REGISTRATION_GATEWAY_SECRET || '';
  fail(secret.length >= 32, 'REGISTRATION_GATEWAY_SECRET must contain at least 32 characters', 500);
  const expected = crypto.createHash('sha256').update(secret).digest();
  app.addHook('onRequest', async r => {
    if (r.url.split('?')[0].startsWith('/health')) return;
    const actual = crypto.createHash('sha256').update(String(r.headers['x-registration-gateway'] || '')).digest();
    fail(crypto.timingSafeEqual(expected, actual), '统一入口认证失败', 401);
    const peer = String(r.headers['x-registration-source-ip'] || '');
    fail(net.isIP(peer), '请求来源无效', 400);
    r.registrationPeer = peer;
  });
  app.addHook('preHandler', async r => {
    const pathname = new URL(r.url, 'http://local').pathname;
    if (pathname.startsWith('/api/admin/v1/redeem-codes') || pathname==='/api/user/v1/redeem')
      fail(false, '请通过统一资金中心和兑换码管理操作', 409);
    if (r.method==='PATCH' && pathname==='/api/admin/v1/settings') {
      const shared=['site_name','brand','announcement','announcements','custom_menus','registration_open','login_slider_enabled','email_verify_enabled','email_domains','smtp'];
      fail(!shared.some(key=>Object.hasOwn(r.body||{},key)), '请通过统一站点设置修改公共配置', 409);
    }
    if (r.method !== 'GET' && /^\/api\/admin\/v1\/users(?:\/[^/]+)?$/.test(pathname))
      fail(false, '请通过星桥账户管理创建或停用账户', 409);
    if (r.method !== 'GET' && pathname === '/api/admin/v1/invites')
      fail(false, '请通过星桥邀请管理调整统一伙伴规则', 409);
  });
}
module.exports = { registerAccess };
