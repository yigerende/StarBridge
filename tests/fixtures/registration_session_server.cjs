// Real registration API against a disposable database. No engine/worker is started.
const path = require('node:path');
const base = path.resolve(__dirname, '../../services/gpt-register');
const { Database } = require(base + '/src/platform/db');
const { cipher } = require(base + '/src/platform/security');
const { Orders } = require(base + '/src/platform/orders');
const { buildPlatform } = require(base + '/src/platform/app');
(async () => {
  const db = new Database(process.env.DATABASE_URL);
  await db.initialize();
  const crypt = cipher(Buffer.alloc(32, 7).toString('base64'));
  await db.query(`INSERT INTO platform_users(id,email,name,password_hash,xufu_user_id)
    VALUES('member-owner','owner@identity.test','Owner','unused','owner')`);
  await db.query(`INSERT INTO platform_orders(id,user_id,quantity,need_oauth,unit_price,source,request_key,request_hash,
    template,attempt_limit,timeout_minutes,status,delivered) VALUES('order-one','member-owner',1,false,50,'api','key','hash','{}',2,20,'completed',1)`);
  await db.query(`INSERT INTO platform_items(id,order_id,position,status,asset_id) VALUES('item-one','order-one',0,'delivered','asset-one')`);
  await db.query(`INSERT INTO platform_accounts(id,user_id,order_id,item_id,asset_id,email,need_oauth,credentials)
    VALUES('account-one','member-owner','order-one','item-one','asset-one','account@example.test',false,$1)`,
    [crypt.encrypt({ asset_id: 'asset-one', email: 'account@example.test', password: 'fixture-password',
      totp_secret: 'JBSWY3DPEHPK3PXP', access_token: 'ORIGINAL_AT', session_token: 'ORIGINAL_COOKIE_TOKEN' })]);
  const app = buildPlatform({ db, crypt, orders: new Orders(db, crypt), secrets: { internal: 'i'.repeat(40) }, cwd: base, proxy: false });
  await app.listen({ host: '127.0.0.1', port: 0 });
  process.stdout.write(JSON.stringify({ port: app.server.address().port }) + '\n');
  process.once('SIGTERM', async () => { await app.close(); await db.close(); process.exit(0); });
})().catch(e => { console.error(e.code || e.name); process.exit(1); });
