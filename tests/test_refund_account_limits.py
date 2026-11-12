"""Real HTTP/private-core admission, admin permissions and isolated wallet checks."""
import asyncio
import unittest
import httpx
import test_refunds as fixture
from backend import db, refunds
from backend.main import app


class RefundAccountLimitTests(unittest.TestCase):
    setUp = fixture.RefundTests.setUp
    tearDown = fixture.RefundTests.tearDown

    def test_pagination_filter_and_sort(self):
        from backend.refund_account_limits import listing
        db.execute("""INSERT INTO refunds(id,user_id,email,credentials,status,fee,fee_status)
          VALUES('prior','u','prior@fixture.invalid',decode('00','hex'),'failed',500,'released')""")
        db.execute("""INSERT INTO refund_account_restrictions(email,reason,first_seen_at,last_seen_at,refund_id,resubmissions)
          SELECT 'stopped'||lpad(n::text,4,'0')||'@fixture.invalid','account_deactivated',
          now()+n*interval '1 second',now(),'prior',n FROM generate_series(1,525)n""")
        for size in (10,50,100,500):
            data=listing(page_size=size,sort_by='resubmissions',sort_dir='desc')
            self.assertEqual(data['total'],525)
            self.assertEqual([r['resubmissions'] for r in data['items']],list(range(525,525-size,-1)))
        self.assertEqual(len(listing(page=2,page_size=500)['items']),25)
        self.assertEqual(listing(q='stopped0525')['total'],1)
        self.assertEqual(listing(status='cleared')['total'],0)

    def test_web_api_limits_release_and_permissions(self):
        db.execute("UPDATE refund_settings SET accepting=true")
        db.execute("""INSERT INTO refunds(id,user_id,email,credentials,status,fee,fee_status)
          VALUES('prior','u','stopped@fixture.invalid',decode('00','hex'),'failed',500,'released')""")
        db.execute("""INSERT INTO refund_account_restrictions(email,reason,first_seen_at,last_seen_at,refund_id)
          VALUES('stopped@fixture.invalid','account_deactivated',now(),now(),'prior')""")

        async def scenario():
            await refunds.start()
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
                    user={'Cookie':'xupay_session=test-session-u'}
                    admin={'Cookie':'xupay_session=test-session-a'}
                    key={'Authorization':'Bearer fixture-api-key-long-enough'}
                    endpoint='/api/admin/refund/account-restrictions'
                    clear=endpoint+'/stopped@fixture.invalid/clear'
                    for h,status in [({},401),(user,403),(key,403)]:
                        self.assertEqual((await c.get(endpoint,headers=h)).status_code,status)
                        self.assertEqual((await c.post(clear,headers=h)).status_code,status)
                    body={'authorized':True,'accounts':[{'email':'STOPPED@fixture.invalid','password':'fixture-only','totp_secret':'JBSWY3DPEHPK3PXP'}]}
                    wallet=db.one("SELECT balance,frozen FROM users WHERE id='u'")
                    for path,headers in [('/api/refund/orders',user),('/api/v1/refund/orders',key)]:
                        r=await c.post(path,headers={**headers,'Idempotency-Key':'blocked-new-request'},json=body)
                        self.assertEqual(r.status_code,409,r.text);self.assertIn('次数已用完',r.text)
                    self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),wallet)
                    self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)
                    info=(await c.get(endpoint,headers=admin)).json()
                    self.assertEqual(info['items'][0]['reason_label'],'上游账号已停用')
                    self.assertEqual(info['items'][0]['status'],'blocked')
                    self.assertTrue(info['items'][0]['first_seen_at'])
                    for size in (10,50,100,500):
                        self.assertEqual((await c.get(endpoint,headers=admin,params={'page_size':size,'sort_by':'resubmissions'})).status_code,200)
                    self.assertEqual((await c.get(endpoint,headers=admin,params={'sort_by':'secret'})).status_code,400)
                    self.assertEqual((await c.get(endpoint,headers=admin,params={'q':'not-found'})).json()['total'],0)
                    cfg=(await c.get('/api/admin/refund/settings',headers=admin)).json()['settings']
                    cfg['deactivated_resubmit_limit']=2
                    r=await c.put('/api/admin/refund/settings',headers=admin,json={'settings':cfg})
                    self.assertEqual(r.status_code,200,r.text)
                    for n in range(2):
                        headers={**key,'Idempotency-Key':'allowed-request-'+str(n)}
                        r=await c.post('/api/v1/refund/orders',headers=headers,json=body)
                        self.assertEqual(r.status_code,200,r.text)
                        rid=r.json()['ids'][0]
                        replay=await c.post('/api/v1/refund/orders',headers=headers,json=body)
                        self.assertTrue(replay.json()['replayed'])
                        self.assertEqual((await c.post('/api/v1/refund/orders/'+rid+'/cancel',headers=key)).status_code,200)
                    self.assertEqual(db.scalar('SELECT resubmissions FROM refund_account_restrictions'),2)
                    self.assertEqual((await c.post('/api/refund/orders',headers={**user,'Idempotency-Key':'third-new-request'},json=body)).status_code,409)
                    self.assertEqual((await c.get(endpoint,headers=admin,params={'status':'blocked'})).json()['total'],1)
                    self.assertEqual((await c.post(clear,headers=admin)).status_code,200)
                    self.assertEqual((await c.post(clear,headers=admin)).status_code,200)
                    self.assertEqual(db.scalar("SELECT count(*) FROM audit_events WHERE action='refund.account.clear'"),1)
                    self.assertEqual((await c.get(endpoint,headers=admin,params={'status':'cleared'})).json()['total'],1)
                    r=await c.post('/api/refund/orders',headers={**user,'Idempotency-Key':'after-clear-request'},json=body)
                    self.assertEqual(r.status_code,200,r.text)
                    self.assertEqual((await c.post('/api/refund/orders/'+r.json()['ids'][0]+'/cancel',headers=user)).status_code,200)
                    self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),wallet)
            finally:
                await refunds.stop()
        asyncio.run(scenario())
