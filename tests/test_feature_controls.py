"""Shared menu switches are enforced for browser sessions and customer API keys."""
import asyncio
import json
import os
import subprocess
import unittest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from starlette.requests import Request
import test_support as fixture
from test_registration_account import ROOT
from backend import db
from backend.common import config, digest
from backend.features import FEATURES


class FeatureControlTests(unittest.TestCase):
    setUp=fixture.SupportTests.setUp
    tearDown=fixture.SupportTests.tearDown

    def clients(self):
        from backend.main import app
        token='xp_live_'+('synthetic_feature_key_'*2)
        db.execute("INSERT INTO api_keys(id,user_id,name,hash,prefix) VALUES('feature-key','u','test',$1,'xp_live_test')",digest(token))
        return [TestClient(app,cookies={'xupay_session':'support-u'}),TestClient(app,headers={'Authorization':'Bearer '+token}),TestClient(app,cookies={'xupay_session':'support-admin'})]

    def flags(self,**values):
        db.execute("UPDATE settings SET value=$1 WHERE key='features'",{key:values.get(key,True) for key in FEATURES})

    def test_every_menu_closes_and_management_remains_available(self):
        member,key,admin=self.clients()
        try:
            self.flags(**{key:False for key in FEATURES})
            for client in (member,admin):
                me=client.get('/api/me')
                self.assertEqual(me.status_code,200)
                self.assertEqual(me.json()['navigation'],[])
                self.assertTrue(all(value is False for value in me.json()['features'].values()))
                self.assertEqual(client.patch('/api/me',json={}).status_code,403)
                for path in ('overview','orders','wallet','keys','invites','membership','renewals','support','order-updates'):
                    self.assertEqual(client.get('/api/'+path).status_code,403,path)
            self.assertEqual(key.get('/api/v1/me').status_code,403)
            self.assertEqual(admin.get('/api/admin/settings').status_code,200)
            self.assertEqual(admin.get('/api/admin/orders').status_code,200)
            self.assertEqual(member.get('/api/admin/settings').status_code,403)
            self.assertEqual(member.get('/api/openapi.json').status_code,403)
            self.assertEqual(member.get('/docs').status_code,403)
            self.assertEqual(member.get('/api/public').json()['site']['support_url'],'')
            self.flags()
            self.assertEqual({row['id'] for row in member.get('/api/me').json()['navigation']},set(FEATURES))
            self.assertEqual(key.get('/api/v1/me').status_code,200)
        finally:
            for client in (member,key,admin):client.close()

    def test_subscription_and_link_gates_are_independent_in_all_entrypoints(self):
        member,key,admin=self.clients()
        db.execute("UPDATE settings SET value=jsonb_set(value,'{subscription_flow}','\"gener_direct_pay\"') WHERE key='payment'")
        db.execute("INSERT INTO products(id,name,description,fee) VALUES('pro','Pro','test',500)")
        payload={'product_id':'pro20x','country':'PH','session':{},'accepted':True,'card':{'number':'4242424242424242','month':'12','year':'2030','cvc':'123'}}
        try:
            with patch('backend.subscription.create_order',new_callable=AsyncMock,return_value={'id':'synthetic-only','status':'queued'}) as submit:
                for flags,blocked,allowed in [({'links':False,'subscribe':True},'link','subscription'),({'links':True,'subscribe':False},'subscription','link')]:
                    self.flags(**flags)
                    for workflow in (blocked,allowed):
                        body={**payload,'workflow':workflow}
                        if workflow=='link':body.pop('card')
                        r=key.post('/api/v1/orders',json=body,headers={'Idempotency-Key':'feature-'+workflow})
                        self.assertEqual(r.status_code,403 if workflow==blocked else 200,r.text)
                self.assertEqual(submit.await_count,2)
            # Disabled web/alternative entries reject before their handlers or validation.
            for enabled,blocked in [('subscribe','link'),('links','subscription')]:
                self.flags(**{'subscribe':enabled=='subscribe','links':enabled=='links'})
                for client in (member,admin):
                    for path in ('/api/orders','/api/cslive-go/orders','/api/cslive-full/orders'):
                        self.assertEqual(client.post(path,json={'workflow':blocked}).status_code,403,path)
            self.assertEqual(db.scalar('SELECT count(*) FROM orders'),0)
            self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)
        finally:
            for client in (member,key,admin):client.close()

    def test_closed_renewal_and_wallet_do_not_disable_subscription_or_order_reads(self):
        member,key,admin=self.clients()
        try:
            self.flags(renewal=False,wallet=False,register=False,accounts=False,refund=False,profile=False,support=False)
            for path in ('renewals','wallet','wallet/ledger','topups','refund/quote'):
                self.assertEqual(key.get('/api/v1/'+path).status_code,403,path)
            self.assertEqual(key.post('/api/v1/renewals',json={}).status_code,403)
            self.assertEqual(key.post('/api/v1/wallet/redeem',json={}).status_code,403)
            self.assertEqual(key.post('/api/v1/refund/orders',json={}).status_code,403)
            self.assertEqual(key.get('/api/v1/orders').status_code,200)
            self.assertEqual(key.get('/api/v1/catalog').status_code,200)
            self.assertEqual(member.get('/api/me').status_code,200)
            self.flags(orders=False)
            from backend.wallet import project_ledger
            records=[{'kind':'charge','business':business,'order_id':'sub','registration_order_id':'reg','refund_order_id':'refund','related_order':{'id':'sub'},'payment':{'state':'succeeded'}} for business in ('registration','subscription','refund')]
            for record in project_ledger(records,{'id':'admin','role':'admin'}):
                for field in ('order_id','registration_order_id','refund_order_id','related_order'):
                    self.assertIsNone(record[field])
            for path in ('orders','orders/fake','refund/orders','refund/orders/fake'):
                self.assertEqual(key.get('/api/v1/'+path).status_code,403,path)
        finally:
            for client in (member,key,admin):client.close()

    def test_registration_web_and_key_routes_use_shared_switches(self):
        token='xp_live_'+('synthetic_feature_key_'*2)
        db.execute("INSERT INTO api_keys(id,user_id,name,hash,prefix) VALUES('feature-key','u','test',$1,'xp_live_test')",digest(token))
        script=r"""
const assert=require('node:assert/strict');
const {Database}=require('./src/platform/db');
const {buildPlatform}=require('./src/platform/app');
const db=new Database(process.env.DATABASE_URL);
const app=buildPlatform({db,orders:{regions:async()=>[],create:async()=>({order:{id:'synthetic',status:'queued'}})},crypt:{},secrets:{},proxy:false});
const gateway={'x-registration-gateway':process.env.REGISTRATION_GATEWAY_SECRET,'x-registration-source-ip':'127.0.0.1','x-requested-with':'gptreg'};
async function flags(changes){await db.query("UPDATE settings SET value=value||$1::jsonb WHERE key='features'",[changes]);}
let session='support-u';
async function request(url,method='GET',payload,api=false){return app.inject({url,method,payload,headers:{...gateway,...(api?{authorization:'Bearer '+process.env.FEATURE_TEST_TOKEN}:{cookie:'xupay_session='+session})}})}
(async()=>{try{
 // Disagreeing old platform flags must have no influence on unified settings.
 await db.query("UPDATE platform_settings SET value=jsonb_set(value,'{features}', $1::jsonb) WHERE id=1",[{register:false,orders:false,accounts:false}]);
 for(session of ['support-u','support-admin'])for(const api of [false,true]){
  const root=api?'/open/v1':'/api/user/v1';
  await flags({register:false,accounts:true,orders:true,keys:true});
  for(const [path,method] of [['products','GET'],['registration-regions','GET'],['registration-orders','POST']])assert.equal((await request(root+'/'+path,method,method==='POST'?{}:undefined,api)).statusCode,403,path);
  assert.equal((await request(root+'/accounts','GET',undefined,api)).statusCode,200);
  assert.equal((await request(root+'/registration-orders','GET',undefined,api)).statusCode,200);
  await flags({register:true,accounts:false,orders:false});
  assert.equal((await request(root+'/registration-orders','POST',{},api)).statusCode,202);
  for(const [path,method] of [['accounts','GET'],['accounts/fake/credentials','GET'],['accounts/export','POST'],['registration-orders','GET'],['registration-orders/fake','GET']])assert.equal((await request(root+'/'+path,method,method==='POST'?{}:undefined,api)).statusCode,403,path);
 }
 await flags({profile:false});
 assert.equal((await request('/api/user/v1/profile-summary')).statusCode,403);
 await flags({keys:false,register:true});
 assert.equal((await request('/open/v1/me','GET',undefined,true)).statusCode,403);
 assert.equal((await request('/api/user/v1/registration-regions')).statusCode,200);
 const admin=await app.inject({url:'/api/admin/v1/settings',method:'PATCH',payload:{features:{register:true}},headers:{...gateway,cookie:'xupay_session=support-admin'}});
 assert.equal(admin.statusCode,409);
 console.log(JSON.stringify({registrationGates:'passed',sharedFlags:'passed',keyRevocation:'passed'}));
}finally{await app.close();await db.close()}})().catch(e=>{console.error(e);process.exit(1)});
"""
        result=subprocess.run(['node','-e',script],cwd=ROOT/'services/gpt-register',env={**os.environ,'XUFU_INTEGRATION':'1','REGISTRATION_GATEWAY_SECRET':'synthetic-gateway-'*3,'FEATURE_TEST_TOKEN':token},capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_feature_configuration_roundtrip_and_recovery(self):
        member,key,admin=self.clients()
        try:
            values={name:False for name in FEATURES}
            response=admin.put('/api/admin/settings/features',json=values)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(admin.get('/api/admin/settings').json()['features'],values)
            self.assertEqual(member.put('/api/admin/settings/features',json={name:True for name in FEATURES}).status_code,403)
            self.assertEqual(config('features'),values)
            values.update(subscribe=True,orders=True,keys=True)
            self.assertEqual(admin.put('/api/admin/settings/features',json=values).status_code,200)
            self.assertEqual({row['id'] for row in member.get('/api/me').json()['navigation']},{'subscribe','orders','keys'})
            self.assertEqual(key.get('/api/v1/orders').status_code,200)
            self.assertEqual(key.get('/api/v1/wallet').status_code,403)
        finally:
            for client in (member,key,admin):client.close()

    def test_notification_permissions_revalidate_without_signing_out(self):
        from backend import order_updates as updates
        member,key,admin=self.clients()
        try:
            self.assertEqual(member.get('/api/admin/order-updates').status_code,403)
            async def receive():return {'type':'http.request','body':b'','more_body':False}
            def request(path,token):
                return Request({'type':'http','method':'GET','path':path,'headers':[(b'cookie',('xupay_session='+token).encode())],'client':('testclient',1),'query_string':b''},receive)
            async def exercise():
                broker=updates.OrderUpdates()
                with patch.object(updates,'updates',broker):
                    stream=(await updates.order_updates(request('/api/order-updates','support-admin'))).body_iterator
                    self.assertIn('ready',await anext(stream))
                    broker.publish({'id':'foreign','user_id':'u'})
                    broker.publish({'id':'own','user_id':'admin'})
                    event=await asyncio.wait_for(anext(stream),2)
                    self.assertIn('own',event);self.assertNotIn('foreign',event)
                    self.flags(orders=False)
                    broker.publish({'resync':True})
                    self.assertIn('forbidden',await anext(stream))
                    await stream.aclose()
                    management=(await updates.admin_order_updates(request('/api/admin/order-updates','support-admin'))).body_iterator
                    self.assertIn('ready',await anext(management))
                    broker.publish({'id':'customer-order','user_id':'u'})
                    self.assertIn('customer-order',await asyncio.wait_for(anext(management),2))
                    db.execute('DELETE FROM sessions WHERE hash=$1',digest('support-admin'))
                    broker.publish({'resync':True})
                    self.assertIn('revoked',await anext(management))
                    await management.aclose()
                    self.assertFalse(broker.clients)
            asyncio.run(exercise())
            self.assertEqual(member.get('/api/me').status_code,200)
        finally:
            for client in (member,key,admin):client.close()


if __name__=='__main__':unittest.main()
