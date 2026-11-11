"""Real private worker bridge and shared wallet on a disposable PostgreSQL database.
No resources are configured; upstream execution cannot run.
"""
import asyncio
import json
import secrets
import time
import os
import threading
from unittest.mock import patch
from psycopg2.pool import ThreadedConnectionPool
import unittest
import httpx
from test_registration_account import RegistrationAccountTests as Fixture
from backend import db, refunds
from backend.common import digest
from backend.seed import DEFAULTS
from backend.main import app


class RefundTests(unittest.TestCase):
    tearDown=Fixture.tearDown

    def setUp(self):
        Fixture.setUp(self)
        # The local cluster also hosts the preview and allows only 40 connections.
        # Exercise backpressure with a smaller pool in this disposable test database.
        db.close()
        db._pool=ThreadedConnectionPool(12,12,os.environ['DATABASE_URL'])
        slots=patch.object(db,'_slots',threading.BoundedSemaphore(12));slots.start();self.addCleanup(slots.stop)
        for key,value in DEFAULTS.items():
            db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        db.execute("UPDATE settings SET value=value||$1::jsonb WHERE key='risk'",{'api_per_minute':10000,'order_per_minute':1000,'edge_per_minute':100000})
        db.execute("UPDATE users SET balance=200000,frozen=0 WHERE id='u'")
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code,role) VALUES('a','admin@refund.test','Admin','synthetic','admin-fixture','admin')")
        db.execute("INSERT INTO user_service_prices(user_id,service,fee) VALUES('u','refund',500)")
        for user in ('u','v','a'):
            db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')",digest('test-session-'+user),user)
        db.execute("INSERT INTO api_keys(id,user_id,name,hash,prefix) VALUES('testkey','u','fixture',$1,'fixture')",digest('fixture-api-key-long-enough'))

    def test_http_wallet_permissions_idempotency_and_parallel_reads(self):
        asyncio.run(self.scenario())

    def test_proxy_scope_and_backup_settings_through_private_bridge(self):
        async def scenario():
            await refunds.start()
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
                    admin={'Cookie':'xupay_session=test-session-a'}
                    user={'Cookie':'xupay_session=test-session-u'}
                    ids=[]
                    for target in ('both','child','mother'):
                        data={'name':'Fixture '+target,'url':'http://fixture.invalid:8080','target':target,'enabled':True}
                        self.assertEqual((await c.post('/api/admin/refund/proxies',headers=user,json=data)).status_code,403)
                        response=await c.post('/api/admin/refund/proxies',headers=admin,json=data)
                        self.assertEqual(response.status_code,200,response.text)
                    info=(await c.get('/api/admin/refund/settings',headers=admin)).json()
                    by={p['target']:p['id'] for p in info['proxies']}
                    cfg=info['settings'];cfg.update(child_proxy_id=by['both'],child_proxy_backups=[by['child']],child_proxy_cooldown_seconds=180)
                    self.assertEqual((await c.put('/api/admin/refund/settings',headers=user,json={'settings':cfg})).status_code,403)
                    response=await c.put('/api/admin/refund/settings',headers=admin,json={'settings':cfg})
                    self.assertEqual(response.status_code,200,response.text)
                    saved=(await c.get('/api/admin/refund/settings',headers=admin)).json()
                    self.assertEqual(saved['settings']['child_proxy_backups'],[by['child']])
                    self.assertEqual(saved['settings']['child_proxy_cooldown_seconds'],180)
                    self.assertNotIn('fixture.invalid',json.dumps(saved))
                    cfg['child_proxy_backups']=[by['mother']]
                    self.assertEqual((await c.put('/api/admin/refund/settings',headers=admin,json={'settings':cfg})).status_code,400)
                    self.assertEqual((await c.delete('/api/admin/refund/proxies/'+by['child'],headers=admin)).status_code,200)
                    self.assertEqual((await c.get('/api/admin/refund/settings',headers=admin)).json()['settings']['child_proxy_backups'],[])
            finally:await refunds.stop()
        asyncio.run(scenario())

    async def scenario(self):
        await refunds.start()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',timeout=30) as c:
                def session(user):return {'Cookie':'xupay_session=test-session-'+user}
                self.assertEqual((await c.get('/api/refund/orders')).status_code,401)
                for path in ('/api/admin/refund/settings','/api/admin/refund/orders','/api/admin/refund/orders/none/timeline'):
                    self.assertEqual((await c.get(path,headers=session('u'))).status_code,403)
                    self.assertEqual((await c.get(path,headers={'Authorization':'Bearer fixture-api-key-long-enough'})).status_code,403)
                price=(await c.get('/api/refund/quote',headers=session('u'))).json()
                # Refund admission must remain independent of subscription/link access.
                db.execute("UPDATE settings SET value=value || '{\"links\":false,\"subscribe\":false}'::jsonb WHERE key='features'")
                self.assertEqual(price['fee'],500)
                self.assertEqual(price['price_source'],'account_override')
                self.assertFalse(price['accepting'])
                data={'authorized':True,'accounts':[{'email':'authorized@fixture.invalid','password':'not-real','totp_secret':'JBSWY3DPEHPK3PXP'}],'idempotency_key':'shared-http-request'}
                self.assertEqual((await c.post('/api/refund/orders',headers=session('u'),json=data)).status_code,400)
                self.assertEqual((await c.put('/api/admin/refund/pricing',headers=session('a'),json={'accepting':True,'service_fee':10000})).status_code,200)
                started=time.monotonic()
                results=await asyncio.gather(*(c.post('/api/refund/orders',headers=session('u'),json=data) for _ in range(50)))
                self.assertEqual({r.status_code for r in results},{200},[(r.status_code,r.json().get('error')) for r in results if r.status_code!=200][:8])
                ids={r.json()['ids'][0] for r in results};self.assertEqual(len(ids),1);id=ids.pop()
                self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),{'balance':200000,'frozen':500})
                self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE business='refund'"),1)
                self.assertEqual((await c.get('/api/refund/orders/'+id,headers=session('v'))).status_code,404)
                self.assertEqual((await c.post('/api/refund/orders/'+id+'/cancel',headers=session('v'),json={})).status_code,404)
                key={'Authorization':'Bearer fixture-api-key-long-enough'}
                public_data={k:v for k,v in data.items() if k!='idempotency_key'}
                self.assertEqual((await c.post('/api/v1/refund/orders',headers=key,json=public_data)).status_code,400)
                self.assertEqual((await c.post('/api/v1/refund/orders',headers=key,json=data)).status_code,400)
                replay=await c.post('/api/v1/refund/orders',headers={**key,'Idempotency-Key':data['idempotency_key']},json=public_data)
                self.assertEqual(replay.status_code,200)
                self.assertEqual(replay.json(),{'ids':[id],'replayed':True})
                detail=await c.get('/api/v1/refund/orders/'+id,headers=key)
                self.assertEqual(detail.status_code,200)
                self.assertNotIn('not-real',detail.text);self.assertNotIn('totp_secret',detail.text)
                self.assertNotIn('lease',detail.text);self.assertNotIn('mother',detail.text)
                # Authenticated public API keys cannot call the private web route.
                self.assertEqual((await c.get('/api/refund/orders',headers=key)).status_code,403)
                reads=await asyncio.gather(*(c.get('/api/refund/orders',headers=session('u' if i%2==0 else 'v')) for i in range(100)))
                self.assertEqual({r.status_code for r in reads},{200},[(r.status_code,r.json().get('error')) for r in reads if r.status_code!=200][:8])
                self.assertEqual([r.json()['total'] for r in reads],[1 if i%2==0 else 0 for i in range(100)])
                held=(await c.get('/api/wallet',headers=session('u'))).json()['ledger'][0]
                self.assertEqual((held['business'],held['available_delta'],held['frozen_delta'],held['refund_order_id']),('refund',-500,500,id))
                cancellations=await asyncio.gather(*(c.post('/api/v1/refund/orders/'+id+'/cancel',headers=key,json={}) for _ in range(24)))
                self.assertEqual({r.status_code for r in cancellations},{200})
                self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),{'balance':200000,'frozen':0})
                self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE business='refund'"),2)
                admin=(await c.get('/api/admin/overview?include_admin=true',headers=session('a'))).json()
                self.assertIn('refund',admin['queues']);self.assertEqual(admin['recent_orders'][0]['business'],'refund')
                self.assertEqual((await c.get('/api/admin/finance?business=refund',headers=session('a'))).status_code,200)
                monitor=await c.get('/api/admin/refund/orders?fee_status=released&status=cancelled&sort=oldest',headers=session('a'))
                self.assertEqual(monitor.status_code,200)
                self.assertEqual(monitor.json()['items'][0]['user_email'],'u@example.test')
                self.assertEqual(monitor.json()['summary']['cancelled'],1)
                self.assertEqual((await c.get('/api/admin/refund/orders?fee_status=frozen',headers=session('a'))).json()['total'],0)
                self.assertEqual((await c.get('/api/admin/refund/orders?end=2000-01-01',headers=session('a'))).json()['total'],0)
                self.assertEqual((await c.get('/api/admin/refund/orders?sort=invalid',headers=session('a'))).status_code,400)
                self.assertEqual((await c.put('/api/admin/refund/settings',headers=session('a'),json={'settings':{'enabled':'invalid'}})).status_code,400)
                timeline=await c.get('/api/admin/refund/orders/'+id+'/timeline',headers=session('a'))
                self.assertEqual(timeline.status_code,200)
                self.assertNotIn('totp_secret',timeline.text);self.assertNotIn('not-real',timeline.text)
                # A real membership discount is used by both quote and order reservation.
                tier=next(t for t in DEFAULTS['tiers']['items'] if t['discount']<100)
                db.execute("UPDATE users SET membership_code=$1 WHERE id='u'",tier['code'])
                db.execute("DELETE FROM user_service_prices WHERE user_id='u' AND service='refund'")
                member_price=(await c.get('/api/v1/refund/quote',headers=key)).json()
                expected=(10000*tier['discount']+50)//100
                self.assertEqual(member_price['fee'],expected)
                self.assertEqual(member_price['saved'],10000-expected)
                self.assertEqual(member_price['price_source'],'membership')
                member_data={'authorized':True,'accounts':[{'email':'member@fixture.invalid','password':'not-real','totp_secret':'JBSWY3DPEHPK3PXP'}],'idempotency_key':'member-discount-request'}
                member_result=await c.post('/api/refund/orders',headers=session('u'),json=member_data)
                self.assertEqual(member_result.status_code,200)
                member_id=member_result.json()['ids'][0]
                self.assertEqual(db.scalar("SELECT fee FROM refunds WHERE id=$1",member_id),expected)
                self.assertEqual(db.scalar("SELECT frozen FROM users WHERE id='u'"),expected)
                self.assertEqual((await c.post('/api/refund/orders/'+member_id+'/cancel',headers=session('u'),json={})).status_code,200)
                self.assertEqual(db.scalar("SELECT frozen FROM users WHERE id='u'"),0)
                db.execute("UPDATE users SET membership_code='' WHERE id='u'")
                db.execute("INSERT INTO user_service_prices(user_id,service,fee) VALUES('u','refund',500)")
                # Disable feature; still allow order follow-up via the order capability.
                db.execute("UPDATE settings SET value=jsonb_set(value,'{refund}','false') WHERE key='features'")
                self.assertEqual((await c.get('/api/v1/refund/quote',headers=key)).status_code,403)
                self.assertEqual((await c.get('/api/v1/refund/orders/'+id,headers=key)).status_code,200)
                # Restart only this private module; persisted wallet/orders survive.
                old=refunds._process
                old.kill()
                await old.wait()
                self.assertFalse(refunds.healthy())
                for _ in range(100):
                    await asyncio.sleep(.05)
                    if refunds._process is not None and refunds._process is not old and refunds._client is not None:break
                self.assertIsNot(refunds._process,old)
                self.assertTrue(refunds.healthy())
                self.assertEqual((await c.get('/api/v1/refund/orders/'+id,headers=key)).status_code,200)
                self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),{'balance':200000,'frozen':0})
                # Synthetic stopped reservation: no usable proxy or mother can run.
                db.execute("INSERT INTO refund_proxies(id,name,secret,enabled) VALUES('offline','Offline',decode('00','hex'),false)")
                db.execute("INSERT INTO refund_mothers(id,name,email,team_id,secret,proxy_id,enabled) VALUES('offline','Offline','offline@fixture.invalid','fixture-team',decode('00','hex'),'offline',false)")
                db.execute("UPDATE refunds SET status='attention' WHERE id=$1",id)
                db.execute("INSERT INTO refund_execution(refund_id,mother_id,step) VALUES($1,'offline','submitting')",id)
                async def recovery():
                    r=await c.get('/api/admin/refund/orders/'+id,headers=session('a'))
                    self.assertEqual(r.status_code,200)
                    return r.json()['recovery']
                self.assertEqual(await recovery(),{'running':False,'needs_review':True,'can_release':False})
                self.assertNotIn('recovery',(await c.get('/api/v1/refund/orders/'+id,headers=key)).json())
                db.execute("UPDATE refund_execution SET step='login' WHERE refund_id=$1",id)
                self.assertEqual(await recovery(),{'running':False,'needs_review':False,'can_release':True})
                db.execute("UPDATE refund_execution SET step='accepted',accepted_at=now(),lease_until=now()+interval '1 minute' WHERE refund_id=$1",id)
                self.assertEqual(await recovery(),{'running':True,'needs_review':False,'can_release':False})
                # References from mothers, global configuration and live/history
                # executions must not veto admin deletion. No usable resource runs.
                db.execute("UPDATE refund_settings SET child_proxy_id='offline'")
                db.execute("UPDATE refund_execution SET child_proxy_id='offline' WHERE refund_id=$1",id)
                self.assertEqual((await c.delete('/api/admin/refund/proxies/offline',headers=session('u'))).status_code,403)
                self.assertEqual((await c.delete('/api/admin/refund/proxies/offline',headers=session('a'))).status_code,200)
                resources=(await c.get('/api/admin/refund/settings',headers=session('a'))).json()
                self.assertEqual(resources['proxies'],[])
                self.assertEqual(resources['settings']['child_proxy_id'],'')
                self.assertEqual(db.scalar('SELECT child_proxy_id FROM refund_execution WHERE refund_id=$1',id),'offline')
                self.assertEqual(db.scalar("SELECT proxy_id FROM refund_mothers WHERE id='offline'"),'offline')
                self.assertEqual(await recovery(),{'running':True,'needs_review':False,'can_release':False})
                print(json.dumps({'refund_http_seconds':round(time.monotonic()-started,3),'concurrent_creates':50,'isolated_reads':100,'concurrent_cancels':24,'http_errors':0}))
        finally:
            await refunds.stop()

if __name__=='__main__':unittest.main()
