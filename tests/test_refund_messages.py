"""Private-core/API customer guidance, synthetic orders and no upstream execution."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
import httpx
from test_refunds import RefundTests as Fixture
from backend import db, refund_messages as notice, refunds, activity
from backend.main import app


class RefundMessageTests(unittest.TestCase):
    setUp = Fixture.setUp
    tearDown = Fixture.tearDown

    def order(self, id='phone', status='failed', fee_status='released', reason='phone_verification_skipped'):
        db.execute("INSERT INTO refund_proxies(id,name,secret) VALUES('p','fixture',$1) ON CONFLICT DO NOTHING", b'fixture')
        db.execute("INSERT INTO refund_mothers(id,name,email,team_id,secret,proxy_id) VALUES('m','fixture','m@example.test','t',$1,'p') ON CONFLICT DO NOTHING", b'fixture')
        db.execute("INSERT INTO refunds(id,user_id,email,credentials,status,fee,fee_status) VALUES($1,'u',$2,$3,$4,500,$5)", id, id+'@example.test', b'fixture', status, fee_status)
        db.execute("INSERT INTO refund_execution(refund_id,mother_id,step,released) VALUES($1,'m',$2,true)", id, 'failed' if status=='failed' else 'login')
        self.login(id)
        self.failure(id,reason)
        db.execute("INSERT INTO refund_events(refund_id,message) VALUES($1,'本次服务未完成，冻结服务费已释放。')", id)
        return db.one('SELECT * FROM refunds WHERE id=$1', id)

    def login(self, id):
        db.execute("INSERT INTO refund_events(refund_id,message,internal) VALUES($1,'正在使用密码和 2FA 登录受邀团队。',true)", id)

    def failure(self, id, reason, side='child', stage='login', code='login_failed'):
        db.execute('INSERT INTO refund_execution_diagnostics(refund_id,diagnostic) VALUES($1,$2)', id,
                   {'code':code,'evidence':{'reason':reason,'stage':stage,'side':side,'event':'failure'}})

    def test_authorized_api_list_detail_activity_and_read_only(self):
        row=self.order()
        before={t:db.all('SELECT * FROM '+t) for t in ('refunds','refund_events','refund_execution','users','ledger')}
        async def check():
            await refunds.start()
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
                    for prefix,headers in (
                        ('/api/refund',{'Cookie':'xupay_session=test-session-u'}),
                        ('/api/v1/refund',{'Authorization':'Bearer fixture-api-key-long-enough'}),
                        ('/api/admin/refund',{'Cookie':'xupay_session=test-session-a'}),
                    ):
                        response=await c.get(prefix+'/orders/phone',headers=headers)
                        self.assertEqual(response.status_code,200,response.text)
                        d=response.json();o=d['order']
                        self.assertEqual((o['status'],o['fee_status']),('failed','released'))
                        self.assertEqual(o['error_code'],notice.PHONE_CODE)
                        self.assertIn(notice.PHONE_REASON,o['message'])
                        self.assertIn('冻结服务费已释放',o['message'])
                        self.assertEqual(d['events'][-1]['message'],o['message'])
                        self.assertNotIn('http_status',o)
                        listing=await c.get(prefix+'/orders',headers=headers)
                        self.assertEqual(listing.json()['items'][0]['message'],o['message'])
                        for private in ('phone_verification_skipped','diagnostic','credentials','proxy_id','team_id'):
                            self.assertNotIn(private,response.text)
                    self.assertEqual((await c.get('/api/refund/orders/phone',headers={'Cookie':'xupay_session=test-session-v'})).status_code,404)
                    self.assertEqual((await c.get('/api/refund/orders/phone')).status_code,401)
                    self.assertEqual((await c.get('/api/admin/refund/orders/phone',headers={'Cookie':'xupay_session=test-session-u'})).status_code,403)
                    self.assertEqual((await c.get('/api/admin/refund/orders/phone',headers={'Authorization':'Bearer fixture-api-key-long-enough'})).status_code,403)
                    user=await c.get('/api/activity',headers={'Cookie':'xupay_session=test-session-u'})
                    self.assertIn(notice.PHONE_REASON,user.json()['items'][0]['message'])
                    admin=await c.get('/api/admin/activity',headers={'Cookie':'xupay_session=test-session-a'})
                    self.assertIn(notice.PHONE_REASON,admin.json()['items'][0]['message'])
                    old=await c.get('/api/v1/activity',headers={'Authorization':'Bearer fixture-api-key-long-enough'})
                    self.assertNotIn('message',old.json()['items'][0])
            finally:
                await refunds.stop()
        asyncio.run(check())
        for table,rows in before.items():
            self.assertEqual(db.all('SELECT * FROM '+table),rows,table)

    def test_frozen_unresolved_zero_fee_and_success(self):
        for state in ('processing','verifying','attention','failed'):
            row=self.order(state,status=state,fee_status='frozen')
            message=notice.messages([row])[state]
            self.assertIn('暂勿重复提交',message)
            self.assertNotIn('已释放',message)
            self.assertNotIn('再重新提交',message)
        self.order('free')
        db.execute("UPDATE refunds SET fee=0 WHERE id='free'")
        self.assertIn('无需服务费',notice.messages(db.all("SELECT * FROM refunds WHERE id='free'"))['free'])
        for state in ('succeeded','cancelled','queued','banned'):
            with patch.object(db,'all',side_effect=AssertionError('no query for unrelated states')):
                self.assertEqual(notice.messages([{'id':'phone','status':state}]),{})

    def test_latest_attempt_other_failures_and_async_stale_trace(self):
        row=self.order()
        self.login('phone')
        self.assertEqual(notice.messages([row]),{})
        self.failure('phone','network_error')
        self.failure('phone','phone_verification_skipped',code='protocol_trace')
        self.assertEqual(notice.messages([row]),{})
        for reason in ('extra_verification','email_verification_after_password','incorrect_password','account_deactivated','mfa_state_conflict'):
            self.failure('phone',reason)
            self.assertEqual(notice.messages([row]),{})
        self.failure('phone','phone_verification_skipped',side='mother')
        self.assertEqual(notice.messages([row]),{})
        self.failure('phone','phone_verification_skipped')
        self.assertIn('phone',notice.messages([row]))

    def test_later_progress_submission_and_snapshot_race_are_not_mislabelled(self):
        row=self.order()
        db.execute("UPDATE refund_execution SET accepted_at=now() WHERE refund_id='phone'")
        self.assertEqual(notice.messages([row]),{})
        db.execute("UPDATE refund_execution SET accepted_at=NULL,step='ready' WHERE refund_id='phone'")
        self.assertEqual(notice.messages([row]),{})
        db.execute("UPDATE refund_execution SET step='failed' WHERE refund_id='phone'")
        db.execute("INSERT INTO refund_events(refund_id,message,internal) VALUES('phone','已确认团队会话，准备提交导出并停用个人空间。',true)")
        self.assertEqual(notice.messages([row]),{})
        self.login('phone');self.failure('phone','phone_verification_skipped')
        db.execute("UPDATE refunds SET updated_at=now() WHERE id='phone'")
        self.assertEqual(notice.messages([row]),{})
        self.assertIn('phone',notice.messages(db.all("SELECT * FROM refunds WHERE id='phone'")))

    def test_500_rows_batched_and_concurrent_reads(self):
        self.order()
        db.execute("""INSERT INTO refunds(id,user_id,email,credentials,status,fee,fee_status)
          SELECT 'batch-'||n,'u',n||'@example.test',$1,'failed',500,'released' FROM generate_series(1,500)n""", b'fixture')
        db.execute("INSERT INTO refund_execution(refund_id,mother_id,step,released) SELECT id,'m','failed',true FROM refunds WHERE id LIKE 'batch-%'")
        db.execute("INSERT INTO refund_events(refund_id,message,internal) SELECT id,'正在使用密码和 2FA 登录受邀团队。',true FROM refunds WHERE id LIKE 'batch-%'")
        db.execute("INSERT INTO refund_execution_diagnostics(refund_id,diagnostic) SELECT id,$1 FROM refunds WHERE id LIKE 'batch-%'", {'code':'login_failed','evidence':{'side':'child','stage':'login','event':'failure','reason':'phone_verification_skipped'}})
        rows=db.all("SELECT * FROM refunds WHERE id LIKE 'batch-%'")
        with patch.object(db,'all',wraps=db.all) as query:
            self.assertEqual(len(notice.messages(rows)),500)
            self.assertEqual(query.call_count,1)
        with ThreadPoolExecutor(max_workers=20) as pool:
            self.assertTrue(all(pool.map(lambda _:len(notice.messages(rows))==500,range(40))))


if __name__=='__main__':
    unittest.main()
