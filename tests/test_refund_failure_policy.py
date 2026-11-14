"""Disposable database + private core; no live upstream or business execution."""
import asyncio
import json
import unittest

import httpx
import test_refunds as fixture
from test_refund_mother_management import token
from backend import db, refunds
from backend.main import app
from backend.refund_mother_upstream import open_secret


class RefundFailurePolicyTests(unittest.TestCase):
    setUp = fixture.RefundTests.setUp
    tearDown = fixture.RefundTests.tearDown

    def test_mother_session_edit_roundtrip_permissions_and_no_secret_list(self):
        async def scenario():
            await refunds.start()
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as c:
                    admin = {'Cookie': 'xupay_session=test-session-a'}
                    user = {'Cookie': 'xupay_session=test-session-u'}
                    proxy = await c.post('/api/admin/refund/proxies', headers=admin,
                                         json={'name':'Fixture', 'url':'http://fixture.invalid:8080', 'enabled':True, 'target':'both'})
                    self.assertEqual(proxy.status_code, 200, proxy.text)
                    resources = (await c.get('/api/admin/refund/settings', headers=admin)).json()
                    at = token()
                    session = json.dumps({'WARNING_BANNER':'synthetic fixture '*2200,
                                          'accessToken':at, 'sessionToken':'fixture-private-session'})
                    payload = {'name':'Editable mother', 'session':session, 'proxy_id':resources['proxies'][0]['id'],
                               'seat_type':'default', 'enabled':True}
                    created = await c.post('/api/admin/refund/mothers', headers=admin, json=payload)
                    self.assertEqual(created.status_code, 200, created.text)
                    rows = (await c.get('/api/admin/refund/resources/mothers', headers=admin)).json()['items']
                    self.assertEqual(len(rows), 1)
                    mid = rows[0]['id']
                    path = '/api/admin/refund/mother-management/'+mid+'/credentials'
                    for headers, expected in [({},401),(user,403),({'Authorization':'Bearer fixture-api-key-long-enough'},403)]:
                        self.assertEqual((await c.get(path, headers=headers)).status_code, expected)
                    value = await c.get(path, headers=admin)
                    self.assertEqual(value.headers['cache-control'], 'no-store')
                    self.assertEqual(value.json()['session'], session)
                    self.assertEqual(value.json()['access_token'], at)
                    self.assertNotIn('fixture-private-session', json.dumps(rows))
                    self.assertNotIn(at, json.dumps(rows))
                    stored = db.scalar('SELECT session_secret FROM refund_mothers WHERE id=$1', mid)
                    self.assertNotIn(b'fixture-private-session', bytes(stored))
                    self.assertEqual(open_secret('mother-session:'+mid, stored), session)
                    # Explicit writable fields are sufficient even after resource stats have been loaded.
                    edit = {k:rows[0][k] for k in ('id','name','email','team_id','proxy_id','seat_type','enabled')}
                    edit.update(name='Renamed mother',session='',refresh_token='')
                    saved = await c.post('/api/admin/refund/mothers',headers=admin,json=edit)
                    self.assertEqual(saved.status_code,200,saved.text)
                    self.assertEqual((await c.get(path,headers=admin)).json()['session'],session)
                    edit.update(session=at)
                    saved = await c.post('/api/admin/refund/mothers',headers=admin,json=edit)
                    self.assertEqual(saved.status_code,200,saved.text)
                    value = (await c.get(path,headers=admin)).json()
                    self.assertEqual(value['session'],'')
                    self.assertEqual(value['access_token'],token())
                    audit = json.dumps(db.all('SELECT action,detail FROM audit_events'),default=str)
                    self.assertNotIn('fixture-private-session',audit)
                    self.assertNotIn(at,audit)
            finally:
                await refunds.stop()
        asyncio.run(scenario())

    def test_target_email_failure_limit_shared_by_web_api_and_admin_clear(self):
        db.execute('UPDATE refund_settings SET accepting=true')
        db.execute("UPDATE users SET balance=100000 WHERE id='a'")
        db.execute("INSERT INTO refunds(id,user_id,email,credentials,status,fee,fee_status) VALUES('prior','u','target@fixture.invalid',decode('00','hex'),'failed',500,'released')")
        db.execute("INSERT INTO refund_account_restrictions(email,reason,first_seen_at,last_seen_at,refund_id,failure_count) VALUES('target@fixture.invalid','consecutive_failures',now(),now(),'prior',4)")
        async def scenario():
            await refunds.start()
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
                    admin={'Cookie':'xupay_session=test-session-a'}
                    user={'Cookie':'xupay_session=test-session-u'}
                    key={'Authorization':'Bearer fixture-api-key-long-enough'}
                    account={'email':'TARGET@fixture.invalid','password':'fixture-only','totp_secret':'JBSWY3DPEHPK3PXP'}
                    body={'authorized':True,'accounts':[account]}
                    wallet=db.one("SELECT balance,frozen FROM users WHERE id='u'")
                    for path,headers in [('/api/refund/orders',user),('/api/v1/refund/orders',key),('/api/refund/orders',admin)]:
                        r=await c.post(path,headers={**headers,'Idempotency-Key':'restricted-four-orders'},json=body)
                        self.assertEqual(r.status_code,409,r.text)
                        self.assertIn('连续办理失败',r.text)
                    self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),wallet)
                    self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)
                    root='/api/admin/refund/account-restrictions'
                    data=(await c.get(root+'?status=blocked&sort_by=failure_count',headers=admin)).json()
                    self.assertEqual(data['total'],1)
                    self.assertEqual(data['items'][0]['failure_count'],4)
                    self.assertEqual(data['items'][0]['failure_limit'],4)
                    clear=root+'/target@fixture.invalid/clear'
                    self.assertEqual((await c.post(clear,headers=user)).status_code,403)
                    self.assertEqual((await c.post(clear,headers=admin)).status_code,200)
                    self.assertEqual(db.scalar('SELECT failure_count FROM refund_account_restrictions'),0)
                    r=await c.post('/api/v1/refund/orders',headers={**key,'Idempotency-Key':'after-four-clear'},json=body)
                    self.assertEqual(r.status_code,200,r.text)
                    rid=r.json()['ids'][0]
                    self.assertEqual((await c.post('/api/v1/refund/orders/'+rid+'/cancel',headers=key)).status_code,200)
                    self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),wallet)
                    cfg=(await c.get('/api/admin/refund/settings',headers=admin)).json()['settings']
                    self.assertEqual(cfg['resource_recovery']['mother_failed_accounts'],2)
                    cfg['resource_recovery'].update(mother_failed_accounts=3,account_failure_limit=6,team_session_failure_limit=2)
                    self.assertEqual((await c.put('/api/admin/refund/settings',headers=admin,json={'settings':cfg})).status_code,200)
                    saved=(await c.get('/api/admin/refund/settings',headers=admin)).json()['settings']['resource_recovery']
                    self.assertEqual((saved['mother_failed_accounts'],saved['account_failure_limit'],saved['team_session_failure_limit']),(3,6,2))
            finally:
                await refunds.stop()
        asyncio.run(scenario())
