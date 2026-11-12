'use strict';
// Synthetic rows in a disposable database; no engine or provider calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {Database}=require('../services/gpt-register/src/platform/db');
const {buildPlatform}=require('../services/gpt-register/src/platform/app');
const {cipher,digest}=require('../services/gpt-register/src/platform/security');
const {financeData}=require('../services/gpt-register/integration/finance');
const {supplierData}=require('../services/gpt-register/integration/suppliers');
const {monitor}=require('../services/gpt-register/integration/risk-reports');
const db=new Database(process.env.DATABASE_URL),cwd=fs.mkdtempSync(path.join(os.tmpdir(),'starbridge-list-'));
async function run(){
 await db.query("UPDATE users SET role='admin' WHERE id='v'");
 for(const id of ['u','v'])await db.query("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')",[digest('fixture-'+id),id]);
 await db.query("INSERT INTO platform_orders(id,user_id,quantity,need_oauth,unit_price,source,request_key,request_hash,template,attempt_limit,timeout_minutes,status) SELECT 'reg'||lpad(n::text,4,'0'),'p',n,false,n,'web','req'||n,'fixture','{}',1,30,'completed' FROM generate_series(1,525)n");
 await db.query("INSERT INTO platform_items(id,order_id,position,status) SELECT id,id,1,'delivered' FROM platform_orders");
 await db.query("INSERT INTO platform_accounts(id,user_id,order_id,item_id,asset_id,email,need_oauth,credentials) SELECT id,'p',id,id,id,id||'@fixture.invalid',false,'NEVER-EXPOSE' FROM platform_orders");
 await db.query("INSERT INTO platform_attempts(id,item_id,run_id,stage,status,cost_snapshot) SELECT id,id,id,'register','succeeded',jsonb_build_object('supplier_records',jsonb_build_object('mail',jsonb_build_array(jsonb_build_object('key',id,'provider',id,'counted',true,'outcome','succeeded','received',true,'currency','USD','confirmed',1,'pending',0)))) FROM platform_items");
 await db.query("INSERT INTO platform_risk_events(dedupe,kind,category,severity,message,user_id,source_ip,account) SELECT 'list'||n,'fixture','access','medium','fixture','p','192.0.2.1','account'||n FROM generate_series(1,525)n");
 await db.query("INSERT INTO platform_risk_traffic(id,user_id,source_ip,channel,operation,requests) SELECT 'list'||n,'p','192.0.2.1','web','op'||lpad(n::text,4,'0'),n FROM generate_series(1,525)n");
 const headers=id=>({'x-registration-gateway':process.env.REGISTRATION_GATEWAY_SECRET,'x-registration-source-ip':'127.0.0.1','x-requested-with':'gptreg',cookie:'xupay_session=fixture-'+id});
 const app=buildPlatform({db,orders:{},crypt:cipher(process.env.MASTER_KEY),secrets:{internal:'fixture-internal'},cwd,proxy:false});
 try{
  await app.ready();
  for(const url of ['/api/user/v1/registration-orders','/api/user/v1/accounts','/api/user/v1/account-orders','/api/admin/v1/orders','/api/admin/v1/accounts','/api/admin/v1/account-orders']){
   const actor=url.includes('/admin/')?'v':'u',field=url.endsWith('/account-orders')?'last_delivered_at':'created_at';
   assert.equal((await app.inject({url})).statusCode,401);
   if(actor==='v')assert.equal((await app.inject({url,headers:headers('u')})).statusCode,403);
   for(const limit of [10,50,100,500]){
    const r=await app.inject({url:url+'?'+new URLSearchParams({limit,sort_by:field,sort_dir:'asc'}),headers:headers(actor)});
    assert.equal(r.statusCode,200,url+': '+r.body.slice(0,300));const d=r.json();assert.equal(d.total,525,url);assert.equal(d.items.length,limit,url);assert.ok(!r.body.includes('NEVER-EXPOSE'));
   }
   const a=(await app.inject({url:url+'?limit=500&sort_by='+field+'&sort_dir=asc',headers:headers(actor)})).json();
   const b=(await app.inject({url:url+'?limit=500&offset=500&sort_by='+field+'&sort_dir=asc',headers:headers(actor)})).json();
   assert.equal(b.items.length,25,url);assert.equal(new Set([...a.items,...b.items].map(r=>r.id||r.order_id)).size,525,url);
   assert.equal((await app.inject({url:url+'?sort_by=unknown',headers:headers(actor)})).statusCode,400,url);
  }
  await db.query("UPDATE platform_accounts SET created_at=now()-interval '73 hours' WHERE id='reg0001'");
  const expired=(await app.inject({url:'/api/user/v1/accounts?limit=500',headers:headers('u')})).json();assert.equal(expired.total,524);
  const own=(await app.inject({url:'/api/user/v1/accounts?limit=500',headers:headers('v')})).json();assert.equal(own.total,0);
  for(const sort_dir of ['asc','desc']){
   const sorted=(await app.inject({url:'/api/user/v1/registration-orders?limit=10&sort_by=quantity&sort_dir='+sort_dir,headers:headers('u')})).json();assert.equal(sorted.items[0].quantity,sort_dir==='asc'?1:525);
   const costs=await supplierData(db,{limit:'500',sort_by:'provider',sort_dir});assert.equal(costs.total,525);assert.equal(costs.items.length,500);
   for(const field of ['success_rate','receipt_rate','cost_cny']){
    const all=await supplierData(db,{limit:'500',sort_by:field,sort_dir});
    assert.equal(all.items.length,500);assert.equal(all.items[0][field],field==='cost_cny'?6.7:100);
   }
   const risk=await monitor(db,{q:'192.0.2.1',limit:'500',view:'traffic',sort_by:'requests',sort_dir});assert.equal(risk.total,525);assert.equal(risk.traffic.length,500);assert.equal(risk.traffic[0].requests,sort_dir==='asc'?1:525);
   const alerts=await monitor(db,{q:'192.0.2.1',limit:'500',sort_by:'account',sort_dir});assert.ok(alerts.total>=525);assert.equal(alerts.items.length,500);
   for(const view of ['users','items'])await financeData(db,{limit:'500',view,sort_by:view==='users'?'email':'created_at',sort_dir});
  }
  console.log('Registration: 6 lists, four page sizes, full sort, 500+25 rows, expiry, owner isolation, finance, suppliers, risk traffic passed');
 }finally{await app.close()}
}
run().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{await db.close();fs.rmSync(cwd,{recursive:true,force:true})});
