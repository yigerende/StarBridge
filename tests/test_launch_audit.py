"""Promotion checks against a disposable database; no real business requests."""
import asyncio
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend import db
from backend.common import digest, uid, decrypt


class LaunchAuditTests:
    @contextmanager
    def risk_policy(self, **changes):
        saved=db.scalar("SELECT value FROM settings WHERE key='risk'")
        db.execute("UPDATE settings SET value=value||$1::jsonb WHERE key='risk'",changes)
        db.execute('DELETE FROM rate_buckets')
        try:yield
        finally:
            db.execute("UPDATE settings SET value=$1 WHERE key='risk'",saved)
            db.execute('DELETE FROM rate_buckets')

    def ip_client(self, client, address):
        result=TestClient(self.client.app,client=(address,1000))
        result.cookies.update(client.cookies)
        return result

    def test_launch_keys_copy_rename_revoke_and_isolation(self):
        owner,_,user=self.new_user();_,_,other=self.new_user()
        key=self.ok(user.post('/api/keys',json={'name':'first'}));token=key['key'];ident=key['id']
        row=db.one('SELECT * FROM api_keys WHERE id=$1',ident)
        self.assertNotEqual(row['secret_enc'],token);self.assertEqual(decrypt(row['secret_enc']),token)
        listing=self.ok(user.get('/api/keys'))['items'];self.assertTrue(listing[0]['copyable'])
        self.assertNotIn(token,json.dumps(listing));self.assertNotIn('secret_enc',listing[0]);self.assertNotIn('hash',listing[0])
        for _ in range(2):
            response=user.post('/api/keys/'+ident+'/reveal',json={})
            self.assertEqual(self.ok(response)['key'],token);self.assertEqual(response.headers['cache-control'],'no-store')
        self.ok(user.patch('/api/keys/'+ident,json={'name':'new purpose'}))
        self.assertEqual(self.ok(user.get('/api/keys'))['items'][0]['name'],'new purpose')
        for client in (other,self.client):
            self.assertEqual(client.post('/api/keys/'+ident+'/reveal',json={}).status_code,404)
            self.assertEqual(client.patch('/api/keys/'+ident,json={'name':'stolen'}).status_code,404)
        for name in ('','   ',None,'长'*81):self.assertEqual(user.patch('/api/keys/'+ident,json={'name':name}).status_code,400)
        api=TestClient(self.client.app,headers={'Authorization':'Bearer '+token})
        self.ok(api.get('/api/v1/wallet'))
        self.assertEqual(api.post('/api/keys/'+ident+'/reveal',json={}).status_code,403)
        self.ok(user.delete('/api/keys/'+ident));self.assertIsNone(db.scalar('SELECT secret_enc FROM api_keys WHERE id=$1',ident))
        self.assertEqual(user.post('/api/keys/'+ident+'/reveal',json={}).status_code,404)
        self.assertEqual(api.get('/api/v1/wallet').status_code,401)

    def test_launch_old_key_retained_only_after_valid_use(self):
        owner,_,user=self.new_user();token='xp_live_'+uid()+uid();ident=uid()
        db.execute('INSERT INTO api_keys(id,user_id,name,hash,prefix) VALUES($1,$2,$3,$4,$5)',ident,owner,'existing',digest(token),'masked')
        self.assertEqual(user.post('/api/keys/'+ident+'/reveal',json={}).status_code,409)
        invalid=TestClient(self.client.app,headers={'Authorization':'Bearer '+token+'x'})
        self.assertEqual(invalid.get('/api/v1/wallet').status_code,401)
        self.assertIsNone(db.scalar('SELECT secret_enc FROM api_keys WHERE id=$1',ident))
        client=TestClient(self.client.app,headers={'Authorization':'Bearer '+token})
        self.ok(client.get('/api/v1/wallet'))
        self.assertEqual(self.ok(user.post('/api/keys/'+ident+'/reveal',json={}))['key'],token)

    def test_launch_ip_rate_shared_users_keys_and_not_spoofable(self):
        _,_,first=self.new_user();_,_,second=self.new_user()
        token=self.ok(second.post('/api/keys',json={'name':'ip-budget'}))['key']
        a=self.ip_client(first,'198.51.100.101');b=self.ip_client(second,'198.51.100.101')
        with self.risk_policy(api_per_minute=3,edge_per_minute=100):
            self.ok(a.get('/api/wallet'));self.ok(b.get('/api/wallet'))
            self.ok(b.get('/api/v1/wallet',headers={'Authorization':'Bearer '+token}))
            blocked=b.get('/api/wallet',headers={'X-Forwarded-For':'203.0.113.1','X-Real-IP':'203.0.113.2','X-Admin':'true'})
            self.assertEqual(blocked.status_code,429);self.assertTrue(1<=int(blocked.headers['retry-after'])<=60)
            self.ok(self.ip_client(second,'198.51.100.102').get('/api/wallet'))
            self.assertEqual(db.scalar("SELECT count(*) FROM risk_events WHERE category='rate_limit' AND ip='198.51.100.101'"),1)
            db.execute("UPDATE rate_buckets SET expires_at=now()-interval '1 second' WHERE key='api:198.51.100.101'")
            self.ok(a.get('/api/wallet'))

    def test_launch_admin_bypasses_request_limits_but_not_auth(self):
        admin=self.ip_client(self.client,'198.51.100.103')
        key=self.ok(admin.post('/api/keys',json={'name':'admin-budget'}))
        with self.risk_policy(api_per_minute=1,edge_per_minute=1,login_per_minute=1,redeem_per_minute=1):
            for _ in range(6):
                self.ok(admin.get('/api/admin/settings'));self.ok(admin.get('/api/wallet'))
                self.ok(admin.get('/api/v1/wallet',headers={'Authorization':'Bearer '+key['key']}))
                self.assertEqual(admin.post('/api/wallet/redeem',json={'code':'invalid'}).status_code,400)
            self.assertEqual(db.scalar("SELECT count(*) FROM rate_buckets WHERE key LIKE '%:198.51.100.103'"),0)
            anonymous=TestClient(self.client.app,client=('198.51.100.104',1))
            self.assertEqual(anonymous.post('/api/auth/login',json={'email':'admin','password':'wrong'}).status_code,401)
            self.assertEqual(anonymous.post('/api/auth/login',json={'email':'admin','password':'wrong'}).status_code,429)
            self.ok(anonymous.post('/api/auth/login',json={'email':'admin','password':'admin'}))
            self.ok(admin.delete('/api/keys/'+key['id']))
            self.assertEqual(admin.get('/api/v1/wallet',headers={'Authorization':'Bearer '+key['key']}).status_code,401)

    def test_launch_concurrent_ip_limit_is_atomic(self):
        _,_,user=self.new_user();token=self.ok(user.post('/api/keys',json={'name':'atomic-budget'}))['key']
        with self.risk_policy(api_per_minute=37,edge_per_minute=1000):
            def send(_):
                c=TestClient(self.client.app,client=('198.51.100.105',1000))
                try:return c.get('/api/v1/wallet',headers={'Authorization':'Bearer '+token}).status_code
                finally:c.close()
            start=time.monotonic()
            with ThreadPoolExecutor(max_workers=25) as pool:results=list(pool.map(send,range(100)))
            self.assertEqual(results.count(200),37);self.assertEqual(results.count(429),63)
            print(json.dumps({'case':'100_requests_25_concurrent_shared_ip','accepted':37,'limited':63,'seconds':round(time.monotonic()-start,3)}))

    def test_launch_risk_settings_complete_and_validated(self):
        from backend.rate_limits import DEFAULTS
        cfg=self.ok(self.client.get('/api/admin/settings'))['risk']
        self.assertLessEqual(set(DEFAULTS),set(cfg))
        for name in DEFAULTS:
            for bad in (True,0,-1,'10'):
                self.assertEqual(self.client.put('/api/admin/settings/risk',json={**cfg,name:bad}).status_code,400)
        self.ok(self.client.put('/api/admin/settings/risk',json=cfg))

    def test_launch_original_engine_requires_admin_on_every_entry(self):
        from backend import registration_gateway as gateway
        _,_,user=self.new_user()
        token=self.ok(user.post('/api/keys',json={'name':'engine-denied'}))['key']
        anonymous=TestClient(self.client.app)
        with patch.dict(os.environ,{'REGISTRATION_URL':'http://127.0.0.1:19999','REGISTRATION_GATEWAY_SECRET':'synthetic-'*8}):
            for path in ('/registration/admin/engine','/registration/admin/engine/proxies','/registration/api/v1/config','/registration/api/v1/runs','/registration/api/admin/v1/settings','/registration/admin-assets/Admin.js'):
                self.assertEqual(anonymous.get(path).status_code,401,path)
                self.assertEqual(user.get(path).status_code,403,path)
                self.assertEqual(anonymous.get(path,headers={'Authorization':'Bearer '+token}).status_code,403,path)
            for path in ('/.env','/backend/common.py','/services/refund-core/runtime/refund_runner.py','/registration/.runtime/platform-secrets.json','/registration/src/platform/db.js'):
                self.assertEqual(anonymous.get(path).status_code,404,path)
        anonymous.close()

    def test_launch_authenticated_http_concurrency_and_responsiveness(self):
        import httpx
        clients=[self.new_user()[2] for _ in range(3)]
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.client.app),base_url='http://testserver',timeout=30) as client:
                slots=asyncio.Semaphore(50)
                async def read(n):
                    async with slots:
                        begin=time.monotonic()
                        response=await client.get(['/api/activity','/api/overview','/api/wallet'][n%3],headers={'Cookie':'xupay_session='+clients[n%3].cookies.get('xupay_session')})
                        self.assertEqual(response.status_code,200,response.text[:120])
                        return time.monotonic()-begin
                start=time.monotonic();elapsed=sorted(await asyncio.gather(*(read(n) for n in range(200))))
                print(json.dumps({'case':'200_authenticated_reads_50_concurrent','errors':0,'seconds':round(time.monotonic()-start,3),'p50':round(elapsed[99],3),'p95':round(elapsed[189],3),'max':round(elapsed[-1],3)}))
        with self.risk_policy(api_per_minute=10000,edge_per_minute=100000):asyncio.run(scenario())
        for client in clients:client.close()
