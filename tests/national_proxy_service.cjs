'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {Database}=require('../services/gpt-register/src/platform/db');
const {buildPlatform}=require('../services/gpt-register/src/platform/app');
const {cipher,digest}=require('../services/gpt-register/src/platform/security');
const {createSubscriptionProxies}=require('../services/gpt-register/integration/subscription-proxies');
const base='/api/admin/v1/subscription-proxies',db=new Database(process.env.DATABASE_URL);
const cwd=fs.mkdtempSync(path.join(os.tmpdir(),'starbridge-country-'));
async function run(){
 await db.query("UPDATE users SET role='admin' WHERE id='u'");
 for(const id of ['u','v'])await db.query("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')",[digest('fixture-'+id),id]);
 const headers=id=>({'x-registration-gateway':process.env.REGISTRATION_GATEWAY_SECRET,'x-registration-source-ip':'127.0.0.1','x-requested-with':'gptreg',cookie:'xupay_session=fixture-'+id});
 const app=buildPlatform({db,orders:{},crypt:cipher(process.env.MASTER_KEY),secrets:{internal:'fixture-internal'},cwd,proxy:false});
 try{
  await app.ready();
  assert.equal((await app.inject({url:base})).statusCode,401);
  assert.equal((await app.inject({url:base,headers:headers('v')})).statusCode,403);
  assert.equal((await app.inject({method:'POST',url:base+'/import',headers:headers('v'),payload:{}})).statusCode,403);
  const imported=await app.inject({method:'POST',url:base+'/import',headers:headers('u'),payload:{content:'http://user:password@one.fixture.invalid:8000\nhttp://two.fixture.invalid:8000',scope:'dynamic',overrides:[{line:1,countryCode:'PH',protocol:'http'},{line:2,countryCode:'PH',protocol:'http'}]}});
  assert.equal(imported.statusCode,200,imported.body);let job;
  for(let i=0;i<50;i++){job=(await app.inject({url:base+'/operations/'+imported.json().operation,headers:headers('u')})).json();if(job.state!=='running')break;await new Promise(r=>setTimeout(r,20))}
  assert.equal(job.state,'completed',JSON.stringify(job));
  const snapshot=(await app.inject({url:base,headers:headers('u')})).json();assert.equal(snapshot.dynamic.total,2);
  assert.equal(snapshot.dynamic.countryPools[0].entries.length,0);
  assert.equal((await db.query("SELECT count(*)::int n FROM audit_events WHERE action='national_proxy.import'")).rows[0].n,1);
  assert.equal(fs.existsSync(path.join(cwd,'.runtime','dynamic-proxy-pools.json')),false);
  const inventory=(await app.inject({url:base+'/inventory',headers:headers('u')})).json();assert.equal(inventory.total,2);assert.ok(!JSON.stringify(inventory).includes('password'));
 }finally{await app.close()}
 // A second isolated service uses the exact original providers with a mock
 // transport. Refresh failures retain cached entries; registration is untouched.
 const providerDir=path.join(cwd,'providers');fs.mkdirSync(providerDir);
 let called=[];
 const service=createSubscriptionProxies(db,providerDir,{fetch:async url=>{called.push(url);return {ok:true,status:200,text:async()=> 'http://a.fixture.invalid:8080\nhttp://b.fixture.invalid:8080'}}});
 await service.initialize();
 for(const provider of ['711proxy','b2proxy','1024proxy','cliproxy']){
  const job=service.start('test-provider','u',()=>service.service.buildAndPullApi({provider,countryCode:'US',count:2,protocol:'http',sessionMinutes:15}));
  while(service.jobs.get(job.operation).state==='running')await new Promise(r=>setTimeout(r,20));
  assert.equal(service.jobs.get(job.operation).state,'completed',JSON.stringify(service.jobs.get(job.operation)));
 }
 assert.equal(called.length,4);
 const before=await service.snapshot();assert.equal(before.dynamic.countryPools[0].sources.length,4);
 service.service.fetch=async()=>{throw Error('synthetic network failure')};
 const pull=service.start('refresh','u',()=>service.service.pullAll());
 while(service.jobs.get(pull.operation).state==='running')await new Promise(r=>setTimeout(r,20));
 assert.equal((await service.snapshot()).dynamic.total,2);
 await service.close();
 console.log('Authenticated isolated catalog, provider reuse, durable inventory and encrypted endpoints passed');
}
run().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{await db.close();fs.rmSync(cwd,{recursive:true,force:true})});
