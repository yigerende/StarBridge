"""Customer guidance and old API contracts against a disposable database, no workers."""
import json
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from test_dashboard import DashboardTests as Fixture
from backend import db, subscription_messages as notice
from backend.common import digest
from backend.main import app
from backend.seed import DEFAULTS


class SubscriptionMessageTests(unittest.TestCase):
    tearDown = Fixture.tearDown
    sub = Fixture.sub

    def setUp(self):
        Fixture.setUp(self)
        for key, value in DEFAULTS.items():
            db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING', key, value)
        for who in ('u', 'v', 'admin'):
            db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')", digest('notice-'+who), who)
        db.execute("INSERT INTO api_keys(id,user_id,name,hash,prefix) VALUES('key','u','fixture',$1,'fixture')", digest('notice-test-api-key-long-enough'))
        self.client = TestClient(app, headers={'Authorization':'Bearer notice-test-api-key-long-enough'})
        self.addCleanup(self.client.close)

    def order(self, id='expired', code='invalid_session', original='session_expired', status='failed', released=True):
        self.sub(id, status=status, activation='review' if status=='succeeded' else 'pending', renewal='review' if status=='succeeded' else 'not_started')
        db.execute("INSERT INTO subscription_runs(order_id,user_id,trace_round) VALUES($1,'u',1)", id)
        db.execute("INSERT INTO payment_trace_events(order_id,phase,state,code,round,diagnostics) VALUES($1,'checkout','failed',$2,1,$3),($1,'complete','failed',$2,1,'{}')", id, code, {'response_error_code':original})
        db.execute("INSERT INTO order_events(order_id,stage,message) VALUES($1,'failed','账号授权失效')", id)
        for kind in (('freeze','unfreeze') if released else ('freeze',)):
            db.execute("INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,description,order_id) VALUES('u',$1,0,1000,0,$2,'fixture',$3)", kind, id+kind, id)
        return db.one('SELECT * FROM orders WHERE id=$1', id)

    def detail(self, id):
        r = self.client.get('/api/v1/orders/'+id)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_failed_session_old_contract_messages_and_read_only(self):
        row = self.order()
        before = db.one('SELECT * FROM orders WHERE id=$1', row['id'])
        d = self.detail(row['id'])
        self.assertEqual(set(d), {'order','events','trace','checkout','payment'})
        self.assertEqual(set(d['payment']), {'state','last4','amount','currency','fee_state','charged_amount','charged_currency','message','enabled'})
        self.assertEqual(d['order']['status'], 'failed')
        self.assertEqual(d['payment']['fee_state'], 'released')
        self.assertEqual(d['payment']['state'], 'failed')
        message = d['payment']['message']
        for expected in ('Session 已失效或无法刷新', '新的完整 Session', '本单未提交付款', '冻结服务费已释放'):
            self.assertIn(expected, message)
        self.assertEqual(d['events'][-1]['message'], message)
        self.assertEqual(d['trace'], [])
        items = self.client.get('/api/v1/orders').json()['items']
        self.assertEqual(items[0]['payment']['message'], message)
        self.assertEqual(set(items[0]['payment']), {'state','last4','amount','currency','fee_state','message'})
        self.assertEqual(db.one('SELECT * FROM orders WHERE id=$1', row['id']), before)
        self.assertEqual(db.scalar('SELECT message FROM order_events WHERE order_id=$1', row['id']), '账号授权失效')
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'), 2)

    def test_missing_mismatch_network_and_stale_round_are_distinct(self):
        for id, code, original, expected in (
            ('missing','invalid_session','session_token_required','缺少刷新所需'),
            ('mismatch','account_mismatch','account_mismatch','与本单办理账号不一致'),
            ('network','timeout','network_timeout','办理未完成'),
        ):
            self.order(id,code,original)
            message = self.detail(id)['payment']['message']
            self.assertIn(expected, message)
            self.assertNotIn('已失效或无法刷新', message)
        # A previous attempt's missing cookie must not override this round's failure.
        self.order('rounds')
        db.execute("UPDATE payment_trace_events SET round=0,diagnostics=$1 WHERE order_id='rounds' AND phase='checkout'", {'response_error_code':'session_token_required'})
        self.assertNotIn('缺少', self.detail('rounds')['payment']['message'])
        db.execute("INSERT INTO payment_trace_events(order_id,phase,state,code,round) VALUES('rounds','complete','failed','timeout',2)")
        self.assertNotIn('Session', self.detail('rounds')['payment']['message'])

    def test_unknown_payment_and_missing_release_never_invite_resubmit(self):
        self.order('submitted')
        db.execute("UPDATE subscription_runs SET payment_submit_started_at=now() WHERE order_id='submitted'")
        self.order('held',released=False)
        self.order('review',status='review')
        for id in ('submitted','held','review'):
            message = self.detail(id)['payment']['message']
            self.assertNotIn('后重新提交', message)
            self.assertNotIn('冻结服务费已释放', message)
            self.assertIn('原订单', message)
        self.assertNotIn('本单未提交付款', self.detail('submitted')['payment']['message'])

    def test_paid_warning_keeps_success_and_resolved_warning_disappears(self):
        self.order('paid',status='succeeded')
        db.execute("UPDATE subscription_runs SET paid_at=now(),payment_submit_started_at=now() WHERE order_id='paid'")
        db.execute("INSERT INTO subscription_followups(order_id,checkpoint_enc,phase,warning_code) VALUES('paid','synthetic','activation','session_expired')")
        d = self.detail('paid')
        self.assertEqual(d['order']['status'], 'succeeded')
        self.assertEqual(d['payment']['state'], 'succeeded')
        self.assertEqual(d['payment']['fee_state'], 'charged')
        self.assertIn('本单已付款成功', d['payment']['message'])
        self.assertIn('不要重新下单或付款', d['payment']['message'])
        self.assertNotIn('已释放', d['payment']['message'])
        db.execute("UPDATE orders SET activation='active',renewal='disabled' WHERE id='paid'")
        self.assertEqual(self.detail('paid')['payment']['message'], '付款成功，服务费已结算。')

    def test_permissions_activity_and_internal_detail(self):
        self.order()
        for who, expected in [('v',404),('u',200)]:
            with TestClient(app, cookies={'xupay_session':'notice-'+who}) as c:
                self.assertEqual(c.get('/api/orders/expired').status_code, expected)
                self.assertEqual(c.get('/api/admin/orders/expired').status_code, 403)
                if who == 'u':
                    self.assertIn('Session 已失效', c.get('/api/activity').json()['items'][0]['message'])
                    self.assertIn('Session 已失效', c.get('/api/subscriptions/expired').json()['events'][-1]['message'])
        self.assertNotIn('message', self.client.get('/api/v1/activity').json()['items'][0])
        with TestClient(app) as c:
            self.assertEqual(c.get('/api/v1/orders/expired').status_code, 401)
        with TestClient(app,cookies={'xupay_session':'notice-admin'}) as c:
            d=c.get('/api/admin/orders/expired').json()
            self.assertIn('Session 已失效', d['payment']['message'])
            self.assertIn('Session 已失效', c.get('/api/admin/activity').json()['items'][0]['message'])
        serialized=json.dumps(self.detail('expired'))
        for private in ('response_error_code','credentials_enc','payment_submit_started_at','checkpoint_enc'):
            self.assertNotIn(private, serialized)

    def test_success_has_no_extra_query_and_500_failures_are_batched(self):
        with patch.object(db,'all',side_effect=AssertionError('success must not query')):
            self.assertEqual(notice.messages([{'id':'ok','status':'succeeded','workflow':'subscription','activation':'active','renewal':'disabled'}]), {})
        self.order()
        db.execute("""INSERT INTO orders(id,user_id,product_id,product_name,email,status,fee,idempotency_key,request_hash,workflow)
          SELECT 'batch-'||n,'u','plus','Plus','fixture@example.test','failed',500,'batch-'||n,'test','subscription' FROM generate_series(1,500)n""")
        db.execute("INSERT INTO subscription_runs(order_id,user_id) SELECT id,'u' FROM orders WHERE id LIKE 'batch-%'")
        db.execute("INSERT INTO payment_trace_events(order_id,phase,state,code) SELECT id,'complete','failed','invalid_session' FROM orders WHERE id LIKE 'batch-%'")
        db.execute("""INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,description,order_id)
          SELECT 'u',kind,0,1000,0,id||kind,'fixture',id FROM orders CROSS JOIN (VALUES('freeze'),('unfreeze'))k(kind) WHERE id LIKE 'batch-%'""")
        rows=db.all("SELECT id,status,workflow FROM orders WHERE id LIKE 'batch-%'")
        with patch.object(db,'all',wraps=db.all) as query:
            result=notice.messages(rows)
            self.assertEqual(query.call_count,1)
            self.assertEqual(len(result),500)
            self.assertTrue(all('冻结服务费已释放' in value for value in result.values()))


if __name__ == '__main__':
    unittest.main()
