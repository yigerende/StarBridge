'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
process.env.XUFU_INTEGRATION = '1';
process.env.REGISTRATION_GATEWAY_SECRET = 'isolated-registration-gateway-test-key';
const { Database } = require('../src/platform/db');
const { Orders } = require('../src/platform/orders');
const { cipher, digest, id } = require('../src/platform/security');
const { identity } = require('../integration/identity');
const { buildPlatform } = require('../src/platform/app');

test('unified registration account, wallet and delivery isolation', { timeout: 120000 }, async t => {
  const connection = process.env.GPTREG_TEST_DATABASE_URL;
  assert.ok(connection, 'An explicit isolated database URL is required');
  const url = new URL(connection);
  assert.equal(url.hostname, '127.0.0.1');
  assert.equal(url.port, '18983');
  const admin = new Database(connection), schema = 'registration_test_' + crypto.randomBytes(7).toString('hex');
  await admin.query(`CREATE SCHEMA ${schema}`);
  url.searchParams.set('options', `-c search_path=${schema}`);
  const db = new Database(url.href);
  let app;
  try {
    await db.query(fs.readFileSync(path.join(__dirname, '../../../backend/schema.sql'), 'utf8'));
    await db.initialize();
    await db.initialize();
    await db.query(`UPDATE platform_settings SET value=value || $1::jsonb`,
      [{ orders_open: true, price_standard: 5, price_oauth: 8, max_open_orders: 20, invitations: { enabled: false, stages: [] } }]);
    const orders = new Orders(db, cipher(crypto.randomBytes(32).toString('base64')));
    app = buildPlatform({ db, orders, crypt: orders.crypt, secrets: { internal: 'test' }, proxy: false });
    await app.ready();
    async function account(balance = 3000, frozen = 700, role = 'member') {
      const uid = id(), session = crypto.randomBytes(32).toString('hex');
      await db.query(`INSERT INTO users(id,email,name,password_hash,invite_code,balance,frozen,role)
        VALUES($1,$2,'测试用户','unused',$3,$4,$5,$6)`, [uid, uid + '@example.test', uid, balance, frozen, role]);
      await db.query("INSERT INTO sessions VALUES($1,$2,now()+interval '1 hour')", [digest(session), uid]);
      return { ...(await identity(db, uid)), cookie: 'xupay_session=' + session };
    }
    const request = (user, method, url, payload, headers = {}) => app.inject({ method, url,
      headers: { 'x-registration-gateway': process.env.REGISTRATION_GATEWAY_SECRET,
        'x-registration-source-ip': '127.0.0.1', 'x-requested-with': 'gptreg',
        ...(user ? { cookie: user.cookie } : {}), ...headers }, ...(payload ? { payload } : {}) });
    const create = (u, quantity = 1, key = id(), oauth = false) => orders.create(u, { quantity, need_oauth: oauth }, key, 'web');
    const items = o => db.query('SELECT * FROM platform_items WHERE order_id=$1 ORDER BY position', [o.order.id]).then(r => r.rows);
    const wallet = u => db.one('SELECT balance::int,frozen::int FROM users WHERE id=$1', [u.xufu_user_id]);
    const credentials = () => ({ asset_id: id(), email: id() + '@example.test', password: 'synthetic-password', totp_secret: 'JBSWY3DPEHPK3PXP' });

    await t.test('integrated registration uses the unified StarBridge brand and icon',async()=>{
      const pub=await request(null,'GET','/api/public');assert.equal(pub.statusCode,200,pub.body);
      assert.equal(pub.json().site_name,'星桥');assert.equal(pub.json().brand.english_name,'StarBridge');
      const expected=fs.readFileSync(path.join(__dirname,'../../../backend/assets/starbridge-icon.svg'),'utf8');
      for(const route of ['/site-icon','/favicon.ico','/site-icon/default']){
        const icon=await request(null,'GET',route);assert.equal(icon.statusCode,200,icon.body);assert.equal(icon.body,expected);
      }
    });

    await t.test('same session, identity and immediate role revocation', async () => {
      const u = await account();
      const me = await request(u, 'GET', '/api/user/v1/me');
      assert.equal(me.statusCode, 200, me.body);
      assert.equal(me.json().points, 23);
      assert.equal((await request(u, 'GET', '/api/v1/config')).statusCode, 403);
      assert.equal((await request(u, 'GET', '/admin-assets/AdminConsole-test.js')).statusCode, 403);
      assert.equal((await app.inject('/api/user/v1/me')).statusCode, 401);
      assert.equal((await request(null, 'GET', '/api/user/v1/me', null, { cookie: 'gptreg_session=fake' })).statusCode, 401);
      await db.query("UPDATE users SET status='disabled' WHERE id=$1", [u.xufu_user_id]);
      assert.equal((await request(u, 'GET', '/api/user/v1/me')).statusCode, 401);
    });
    await t.test('page identity reads do not wait for wallet settlement locks',async()=>{
      const u=await account(),lock=await db.pool.connect();
      try{
        await lock.query('BEGIN');await lock.query('SELECT id FROM users WHERE id=$1 FOR UPDATE',[u.xufu_user_id]);
        const result=await Promise.race([identity(db,u.xufu_user_id),new Promise((_,reject)=>setTimeout(()=>reject(Error('page read locked wallet')),1000))]);
        assert.equal(result.id,u.id);
      }finally{await lock.query('ROLLBACK');lock.release()}
    });
    await t.test('only unified redemption and site administration are accepted',async()=>{
      const u=await account(1000,0,'admin');
      for(const [url,payload] of [['/api/admin/v1/redeem-codes',{points:1,count:1}],['/api/user/v1/redeem',{code:'GR-synthetic'}]]){
        assert.equal((await request(u,'POST',url,payload)).statusCode,409);
      }
      assert.equal((await request(u,'PATCH','/api/admin/v1/settings',{announcements:[]})).statusCode,409);
      assert.equal((await request(u,'PATCH','/api/admin/v1/settings',{orders_open:true})).statusCode,200);
      assert.equal(await db.one('SELECT count(*)::int AS n FROM platform_redeem_codes').then(x=>x.n),0);
    });
    await t.test('reserve is atomic, idempotent and does not charge', async () => {
      const u = await account(), key = id();
      const results = await Promise.all(Array.from({ length: 60 }, () => create(u, 2, key)));
      assert.equal(new Set(results.map(r => r.order.id)).size, 1);
      assert.deepEqual(await wallet(u), { balance: 3000, frozen: 1700 });
      assert.equal((await db.one('SELECT count(*)::int n FROM ledger WHERE user_id=$1', [u.xufu_user_id])).n, 1);
      await assert.rejects(create(u, 1, key), /幂等键/);
    });
    await t.test('subscription and registration contend for the same available balance', async () => {
      const u = await account(1000, 0);
      const outcomes = await Promise.allSettled([
        create(u),
        db.tx(async q => (await q.query('UPDATE users SET frozen=frozen+600 WHERE id=$1 AND balance-frozen>=600 RETURNING id', [u.xufu_user_id])).rowCount),
      ]);
      const w = await wallet(u);
      assert.ok(w.frozen === 500 || w.frozen === 600);
      assert.equal(w.balance, 1000);
      assert.equal((outcomes[0].status === 'fulfilled' ? 1 : 0) +
        (outcomes[1].status === 'fulfilled' && outcomes[1].value === 1 ? 1 : 0), 1);
    });
    await t.test('partial delivery charges successes once and preserves subscription freezes', async () => {
      const u = await account(), o = await create(u, 3), rows = await items(o);
      const delivered = credentials();
      await Promise.all(Array.from({ length: 40 }, () => orders.finish(rows[0].id, delivered)));
      await orders.finish(rows[1].id, credentials());
      await Promise.all(Array.from({ length: 40 }, () => orders.finish(rows[2].id, null)));
      assert.deepEqual(await wallet(u), { balance: 2000, frozen: 700 });
      const order = await db.one('SELECT status,charged,released,delivered FROM platform_orders WHERE id=$1', [o.order.id]);
      assert.deepEqual(order, { status: 'partial', charged: 10, released: 5, delivered: 2 });
      assert.equal((await db.one('SELECT count(*)::int n FROM ledger WHERE user_id=$1', [u.xufu_user_id])).n, 4);
      const reserve = await db.one('SELECT (reserved-charged-released)::int remaining FROM registration_reservations WHERE order_id=$1', [o.order.id]);
      assert.equal(reserve.remaining, 0);
      await orders.finish(rows[0].id, null);
      assert.deepEqual(await wallet(u), { balance: 2000, frozen: 700 });
    });
    await t.test('missing OAuth credential never charges and retains retry reservation', async () => {
      const u = await account(), o = await create(u, 1, id(), true), [item] = await items(o);
      await assert.rejects(orders.finish(item.id, credentials()), /交付数据不完整/);
      assert.deepEqual(await wallet(u), { balance: 3000, frozen: 1500 });
      assert.equal((await db.one('SELECT count(*)::int n FROM platform_accounts WHERE item_id=$1', [item.id])).n, 0);
      await orders.finish(item.id, null);
      assert.deepEqual(await wallet(u), { balance: 3000, frozen: 700 });
    });
    await t.test('wallet write failure rolls back account delivery, item and both ledgers', async () => {
      const u = await account(), o = await create(u), [item] = await items(o);
      await db.query(`CREATE FUNCTION fail_test_charge() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.kind='charge' THEN RAISE EXCEPTION 'synthetic ledger failure'; END IF; RETURN NEW; END $$;
        CREATE TRIGGER fail_test_charge BEFORE INSERT ON ledger FOR EACH ROW EXECUTE FUNCTION fail_test_charge()`);
      try { await assert.rejects(orders.finish(item.id, credentials()), /synthetic ledger failure/); }
      finally { await db.query('DROP TRIGGER fail_test_charge ON ledger; DROP FUNCTION fail_test_charge()'); }
      assert.deepEqual(await wallet(u), { balance: 3000, frozen: 1200 });
      assert.equal((await db.one('SELECT count(*)::int n FROM platform_accounts WHERE item_id=$1', [item.id])).n, 0);
      assert.equal((await db.one("SELECT count(*)::int n FROM platform_ledger WHERE reference=$1", ['charge:' + item.id])).n, 0);
      await orders.finish(item.id, credentials());
      assert.deepEqual(await wallet(u), { balance: 2500, frozen: 700 });
    });
    await t.test('unbacked release cannot consume subscription frozen balance', async () => {
      const u = await account();
      await assert.rejects(db.tx(q => q.query("SELECT registration_post_ledger($1,$2,'release',5,-5,'invalid')", [u.id, 'release:' + id()])), /reservation/);
      assert.deepEqual(await wallet(u), { balance: 3000, frozen: 700 });
    });
    await t.test('disabled or deleted user cannot submit but accepted failures still release', async () => {
      const u = await account(), o = await create(u), [item] = await items(o);
      await db.query("UPDATE users SET status='disabled',deleted_at=now() WHERE id=$1", [u.xufu_user_id]);
      await assert.rejects(create(u), /账户已停用/);
      await orders.finish(item.id, null);
      assert.deepEqual(await wallet(u), { balance: 3000, frozen: 700 });
    });
    await t.test('shared API key works and revocation is immediate', async () => {
      const u = await account(), token = 'xp_live_' + crypto.randomBytes(32).toString('base64url');
      await db.query("INSERT INTO api_keys(id,user_id,name,hash,prefix) VALUES($1,$2,'test',$3,'test')", [id(), u.xufu_user_id, digest(token)]);
      const headers = { authorization: 'Bearer ' + token };
      assert.equal((await request(null, 'GET', '/open/v1/me', null, headers)).statusCode, 200);
      await db.query('UPDATE api_keys SET revoked=true WHERE user_id=$1', [u.xufu_user_id]);
      assert.equal((await request(null, 'GET', '/open/v1/me', null, headers)).statusCode, 401);
    });
    await t.test('user cannot read another registration order or delivery', async () => {
      const a = await account(), b = await account(), o = await create(a);
      assert.equal((await request(b, 'GET', '/api/user/v1/registration-orders/' + o.order.id)).statusCode, 404);
    });
    await t.test('authentication and concurrent order mutations do not invert member locks', async () => {
      const u=await account(100000,0);
      await Promise.all(Array.from({length:8},async()=>{
        const o=await create(u),[item]=await items(o);
        await Promise.all([identity(db,u.xufu_user_id),orders.finish(item.id,credentials())]);
      }));
      assert.deepEqual(await wallet(u),{balance:96000,frozen:0});
    });
    await t.test('one invitation rule snapshots registration orders and credits the shared wallet once', async () => {
      const inviter=await account(1000,0),u=await account(3000,0);
      await db.query('UPDATE users SET invited_by=$2 WHERE id=$1',[u.xufu_user_id,inviter.xufu_user_id]);
      await identity(db,u.xufu_user_id);
      await db.query("INSERT INTO settings(key,value) VALUES('invites',$1) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        [{enabled:true,stages:[{until_days:30,rate_percent:20},{until_days:60,rate_percent:10},{until_days:90,rate_percent:5}]}]);
      const o=await create(u),[item]=await items(o);
      await db.query("UPDATE settings SET value=jsonb_set(value,'{enabled}','false') WHERE key='invites'");
      await Promise.all(Array.from({length:4},()=>orders.finish(item.id,credentials())));
      assert.deepEqual(await wallet(inviter),{balance:1100,frozen:0});
      assert.equal((await db.one('SELECT amount,rate_percent FROM platform_partner_rewards WHERE order_id=$1',[o.order.id])).rate_percent,20);
      const next=await create(u);assert.equal(await db.one('SELECT * FROM platform_partner_candidates WHERE order_id=$1',[next.order.id]),undefined);
    });
    await t.test('reinitialization preserves unified balances, registration configuration and subscription settings', async () => {
      const u = await account(1234, 101);
      await db.query("INSERT INTO settings(key,value) VALUES('payment','{\"enabled\":false,\"marker\":\"preserve\"}') ON CONFLICT(key) DO UPDATE SET value=excluded.value");
      await db.initialize();
      assert.deepEqual(await wallet(u), { balance: 1234, frozen: 101 });
      assert.equal((await db.settings()).price_standard, 5);
      assert.equal((await db.one("SELECT value FROM settings WHERE key='payment'")).value.marker, 'preserve');
    });
    await db.query("INSERT INTO settings(key,value) VALUES('tiers',$1) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [{items:[
      {code:'L1',name:'Basic',threshold:0,discount:100},
      {code:'L2',name:'Silver',threshold:1000,discount:95},
      {code:'L3',name:'Free',threshold:1000000,discount:0},
    ]}]);
    await db.query("INSERT INTO settings(key,value) VALUES('membership_pricing','{\"registration_standard\":true,\"registration_oauth\":true,\"refund\":true,\"subscription\":{}}')");
    const member = async (balance=3000) => {
      const u=await account(balance,0);
      await db.query("UPDATE users SET membership_code='L2' WHERE id=$1",[u.xufu_user_id]);
      return u;
    };
    const productQuote = async u => {
      const r=await request(u,'GET','/api/user/v1/products');
      assert.equal(r.statusCode,200,r.body);return r.json();
    };
    await t.test('membership quote, API admission and partial settlement use the same fractional price', async () => {
      const u=await member(),quote=await productQuote(u);
      assert.equal(quote.membership.current.code,'L2');
      assert.deepEqual(quote.products.map(p=>[p.standard_price,p.unit_price]),[[5,4.75],[8,7.6]]);
      const token='xp_live_'+crypto.randomBytes(32).toString('base64url');
      await db.query("INSERT INTO api_keys(id,user_id,name,hash,prefix) VALUES($1,$2,'pricing',$3,'test')",[id(),u.xufu_user_id,digest(token)]);
      const headers={authorization:'Bearer '+token,'idempotency-key':id()};
      const r=await request(null,'POST','/open/v1/registration-orders',{quantity:3,need_oauth:false},headers);
      assert.equal(r.statusCode,202,r.body);
      const order=r.json();assert.equal(order.unit_price,4.75);assert.equal(order.price_snapshot.membership.current.code,'L2');
      assert.deepEqual(await wallet(u),{balance:3000,frozen:1425});
      const rows=await items({order});
      await db.query("UPDATE users SET membership_code='L3' WHERE id=$1",[u.xufu_user_id]);
      const replay=await request(null,'POST','/open/v1/registration-orders',{quantity:3,need_oauth:false},headers);
      assert.equal(replay.statusCode,200,replay.body);assert.equal(replay.json().unit_price,4.75);
      await Promise.all(Array.from({length:3},()=>orders.finish(rows[0].id,credentials())));
      await orders.finish(rows[1].id,credentials());await orders.finish(rows[2].id,null);
      assert.deepEqual(await wallet(u),{balance:2050,frozen:0});
      assert.deepEqual(await db.one('SELECT charged,released FROM platform_orders WHERE id=$1',[order.id]),{charged:9.5,released:4.75});
      await db.initialize();
      assert.equal((await db.one('SELECT unit_price FROM platform_orders WHERE id=$1',[order.id])).unit_price,4.75);
      assert.deepEqual(await wallet(u),{balance:2050,frozen:0});
    });
    await t.test('OAuth quantity and failure release preserve cents and reject insufficient funds', async () => {
      const u=await member(2280),o=await create(u,3,id(),true);
      assert.deepEqual(await wallet(u),{balance:2280,frozen:2280});
      for(const row of await items(o))await orders.finish(row.id,null);
      assert.deepEqual(await wallet(u),{balance:2280,frozen:0});
      const poor=await member(474);await assert.rejects(create(poor),/可用积分不足/);
      assert.deepEqual(await wallet(poor),{balance:474,frozen:0});
    });
    await t.test('manual expiry and separate registration prices match the unified calculator', async () => {
      const {execFileSync}=require('node:child_process');
      const u=await member();
      for(const change of [
        "membership_until=now()+interval '1 day'",
        "membership_until=now()-interval '1 day'",
        "membership_until=NULL,fee_override=123",
        "fee_override=0",
      ]) {
        await db.query(`UPDATE users SET ${change} WHERE id=$1`,[u.xufu_user_id]);
        const expected=JSON.parse(execFileSync('python',['-c',
          'import json,sys;from backend.membership import membership_for,fee_for;from backend import db;m=membership_for(sys.argv[1]);print(json.dumps([fee_for(m,500,"registration_standard"),fee_for(m,800,"registration_oauth")]));db.close()',u.xufu_user_id],
          {cwd:path.resolve(__dirname,'../../..'),env:{...process.env,DATABASE_URL:url.href.replaceAll('+','%20')},timeout:15000,encoding:'utf8'}));
        assert.deepEqual((await productQuote(u)).products.map(p=>Math.round(p.unit_price*100)),expected);
      }
      await db.query("INSERT INTO user_service_prices(user_id,service,fee) VALUES($1,'registration_standard',0),($1,'registration_oauth',999)",[u.xufu_user_id]);
      assert.deepEqual((await productQuote(u)).products.map(p=>p.unit_price),[0,9.99]);
      const o=await create(u),[row]=await items(o);await orders.finish(row.id,credentials());
      assert.equal(o.order.unit_price,0);assert.deepEqual(await wallet(u),{balance:3000,frozen:0});
    });
    await t.test('OAuth can opt out of membership while standard registration remains discounted', async () => {
      const u=await member();
      await db.query("UPDATE settings SET value=jsonb_set(value,'{registration_oauth}','false') WHERE key='membership_pricing'");
      const quote=await productQuote(u);
      assert.deepEqual(quote.products.map(p=>[p.unit_price,p.membership_eligible,p.price_source]),[[4.75,true,'membership'],[8,false,'standard']]);
      const accepted=await create(u,1,id(),true);
      assert.equal(accepted.order.unit_price,8);assert.deepEqual(await wallet(u),{balance:3000,frozen:800});
      await db.query("INSERT INTO user_service_prices(user_id,service,fee) VALUES($1,'registration_oauth',0)",[u.xufu_user_id]);
      const own=await productQuote(u);assert.equal(own.products[1].unit_price,0);assert.equal(own.products[1].price_source,'account_override');assert.equal(own.products[1].membership_eligible,false);
      const [item]=await items(accepted);await orders.finish(item.id,{...credentials(),oauth:{refresh_token:'synthetic-refresh-token'}});
      assert.deepEqual(await wallet(u),{balance:2200,frozen:0});
      await db.query("UPDATE settings SET value=jsonb_set(value,'{registration_oauth}','true') WHERE key='membership_pricing'");
    });
    await t.test('settled subscription spend grants the identical automatic registration tier', async () => {
      const u=await account(5000,0),oid=id();
      await db.query("INSERT INTO products(id,name,description,fee) VALUES('pricing-test','Test','',1000)");
      await db.query("INSERT INTO orders(id,user_id,product_id,product_name,email,status,fee,idempotency_key,request_hash) VALUES($1,$2,'pricing-test','Test','fixture@example.test','succeeded',1000,$1,'test')",[oid,u.xufu_user_id]);
      await db.query("INSERT INTO ledger(user_id,order_id,kind,amount,balance_after,frozen_after,reference,description) VALUES($1,$2,'charge',-1000,5000,0,'pricing-test','fixture')",[u.xufu_user_id,oid]);
      const quote=await productQuote(u);assert.equal(quote.membership.current.code,'L2');assert.equal(quote.products[0].unit_price,4.75);
    });
  } finally {
    if (app) await app.close();
    await db.close();
    await admin.query(`DROP SCHEMA ${schema} CASCADE`);
    await admin.close();
  }
});
