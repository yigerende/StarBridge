"""Real isolated DB; IPRoyal API is mocked. No payment or external request runs."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_registration_account as fixture
from backend import db, country_pool, subscription, subscription_cslive_go
from backend import subscription_retry as retry, subscription_recovery as recovery
from backend.checkout_errors import CheckoutError
from backend.common import encrypt, now
from backend.seed import DEFAULTS


class SubscriptionProxyRestartTests(unittest.TestCase):
    tearDown=fixture.RegistrationAccountTests.tearDown

    def setUp(self):
        fixture.RegistrationAccountTests.setUp(self)
        for key,value in DEFAULTS.items():
            db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        db.execute("UPDATE settings SET value=value||$1::jsonb WHERE key='proxy'",{'mode':'country_pool'})
        db.execute("UPDATE settings SET value=value||$1::jsonb WHERE key='payment'",{'network_restart_limit':2})
        db.execute("UPDATE settings SET value=value||$1::jsonb WHERE key='scheduler'",{'retry_delay_seconds':17})
        with db.transaction() as q:
            country_pool.write(q,dict(country_pool.DEFAULTS,token_enc=encrypt('fixture-token'),username='fixture',
                password_enc=encrypt('fixture-password'),countries=[{'code':'PH'}]))
        db.execute("INSERT INTO products(id,name,description,fee) VALUES('plus','Plus','fixture',300)")

    def order(self, id='order', flow='cslive_go_direct_pay', checkout=True):
        db.execute("INSERT INTO orders(id,user_id,product_id,product_name,email,country,status,fee,idempotency_key,request_hash,workflow) VALUES($1,'u','plus','Plus','fixture@example.test','PH','processing',300,$1,'fixture','subscription')",id)
        db.execute("INSERT INTO subscription_runs(order_id,user_id,subscription_flow,billing_snapshot) VALUES($1,'u',$2,$3)",id,flow,{'country':'US','city':'fixture'})
        if checkout:
            retry.save_checkout(id,flow,{'ok':True,'hostedUrl':'https://fixture.invalid/cs_old','checkoutSessionId':'cs_old'},{'cookies':'fixture-context'})
        db.execute("INSERT INTO country_pool_sessions(session_id) VALUES($1)",(id+'old00000')[:8])
        db.execute("INSERT INTO order_proxy_sessions(order_id,resource_id,endpoint_enc,expires_at,source_mode,session_ref) VALUES($1,'iproyal-country-pool',$2,now()+interval '1 hour','country_pool',$3)",id,encrypt('http://fixture:old@geo.iproyal.com:12321'),(id+'old00000')[:8])
        return id

    def generated(self,session):
        return [f'geo.iproyal.com:12321:fixture:fixture-password_country-ph_session-{session}_lifetime-1h']

    def test_network_retry_discards_old_proxy_checkout_and_context_for_both_flows(self):
        for index,module in enumerate((subscription,subscription_cslive_go)):
            with self.subTest(flow=module.FLOW):
                id=self.order('flow'+str(index),module.FLOW)
                wallet=db.one("SELECT balance,frozen FROM users WHERE id='u'")
                before=db.one('SELECT * FROM subscription_runs WHERE order_id=$1',id)
                with patch.object(country_pool,'api') as api:
                    self.assertTrue(retry.handle_failure(id,CheckoutError('network_error')))
                    api.assert_not_called()
                run=db.one('SELECT * FROM subscription_runs WHERE order_id=$1',id)
                self.assertIsNone(retry.load_checkout(run,module.FLOW))
                self.assertEqual((run['checkout_session_id'],run['checkout_url_enc'],run['checkout_checkpoint_enc']),('','',''))
                self.assertEqual(run['billing_snapshot'],before['billing_snapshot'])
                self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),wallet)
                self.assertEqual(db.scalar('SELECT count(*) FROM ledger WHERE order_id=$1',id),0)
                self.assertIsNone(db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1',id))
                self.assertGreater((run['network_retry_at']-now()).total_seconds(),10)
                event=db.one('SELECT diagnostics FROM payment_trace_events WHERE order_id=$1 ORDER BY id DESC LIMIT 1',id)['diagnostics']
                self.assertEqual(event['proxy_refresh_pending'],True)
                self.assertEqual(event['checkout_reused'],False)
                db.execute("UPDATE orders SET status='processing' WHERE id=$1",id)
                old_session=(id+'old00000')[:8];new_session='fresh00'+str(index)
                with patch.object(country_pool,'api',side_effect=[self.generated(old_session),self.generated(new_session)]) as api:
                    url=module._acquire_order_proxy(id,'PH',600)
                    self.assertIn(new_session,url)
                    self.assertEqual(api.call_count,2)
                    self.assertEqual(module._acquire_order_proxy(id,'PH',600),url)
                    self.assertEqual(api.call_count,2)
                self.assertEqual(db.scalar('SELECT session_ref FROM order_proxy_sessions WHERE order_id=$1',id),new_session)

    def test_failed_checkout_rotates_and_concurrent_failure_delivery_counts_once(self):
        id=self.order(checkout=False)
        with ThreadPoolExecutor(max_workers=4) as threads:
            results=list(threads.map(lambda _:retry.handle_failure(id,RuntimeError('network_error')),range(4)))
        self.assertEqual(results,[True]*4)
        self.assertEqual(db.scalar('SELECT network_retry_count FROM subscription_runs WHERE order_id=$1',id),1)
        self.assertEqual(db.scalar("SELECT count(*) FROM order_events WHERE order_id=$1 AND stage='network_retry'",id),1)
        self.assertIsNone(db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1',id))

    def test_no_rotation_for_paid_uncertain_or_non_network_outcomes(self):
        cases=('paid','submitted','review','declined','forbidden','exhausted','disabled','device','capacity')
        for case in cases:
            with self.subTest(case=case):
                id=self.order(case,checkout=case!='device')
                exc=CheckoutError('network_error')
                if case=='paid':
                    db.execute("UPDATE subscription_runs SET paid_at=now() WHERE order_id=$1",id)
                    db.execute("UPDATE orders SET status='succeeded' WHERE id=$1",id)
                if case=='submitted':retry.mark_payment_submission(id)
                if case=='review':db.execute("UPDATE orders SET status='review' WHERE id=$1",id)
                if case=='declined':exc=RuntimeError('payment_declined')
                if case=='forbidden':exc=CheckoutError('http_403')
                if case=='exhausted':db.execute('UPDATE subscription_runs SET network_retry_count=2 WHERE order_id=$1',id)
                if case=='disabled':db.execute("UPDATE settings SET value=jsonb_set(value,'{network_restart_limit}','0') WHERE key='payment'")
                if case=='device':exc=CheckoutError('device_id_missing')
                if case=='capacity':exc=country_pool.PoolError('pool_capacity')
                binding=db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1',id)
                checkpoint=db.scalar('SELECT checkout_checkpoint_enc FROM subscription_runs WHERE order_id=$1',id)
                retry.handle_failure(id,exc)
                self.assertEqual(db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1',id),binding)
                self.assertEqual(db.scalar('SELECT checkout_checkpoint_enc FROM subscription_runs WHERE order_id=$1',id),checkpoint)
                if case=='submitted':
                    self.assertEqual(db.scalar('SELECT status FROM orders WHERE id=$1',id),'review')
                    self.assertIsNotNone(db.scalar('SELECT payment_submit_started_at FROM subscription_runs WHERE order_id=$1',id))
                db.execute("UPDATE settings SET value=jsonb_set(value,'{network_restart_limit}','2') WHERE key='payment'")

    def test_paid_followup_reschedules_with_original_proxy_and_interval(self):
        id=self.order()
        retry.mark_payment_submission(id,{'flow':subscription_cslive_go.FLOW,'checkout_id':'cs_old'})
        db.execute("UPDATE subscription_runs SET paid_at=now() WHERE order_id=$1",id)
        db.execute("UPDATE orders SET status='succeeded',activation='active',renewal='review' WHERE id=$1",id)
        binding=db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1',id)
        with db.transaction() as q:
            recovery.paid(q,id)
            row=q.one('SELECT * FROM subscription_recoveries WHERE order_id=$1',id)
            recovery._reschedule(q,id,row,'followup','network_error')
        self.assertEqual(db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1',id),binding)
        self.assertEqual(db.scalar('SELECT state FROM subscription_recoveries WHERE order_id=$1',id),'queued')
        self.assertGreater(db.scalar('SELECT extract(epoch FROM next_at-now()) FROM subscription_recoveries WHERE order_id=$1',id),1)
        self.assertEqual(db.scalar('SELECT network_retry_count FROM subscription_runs WHERE order_id=$1',id),0)
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger WHERE order_id=$1',id),0)

    def test_new_proxy_api_failure_cannot_reuse_retired_session(self):
        id=self.order()
        retry.handle_failure(id,CheckoutError('network_error'))
        db.execute("UPDATE orders SET status='processing' WHERE id=$1",id)
        with patch.object(country_pool,'api',side_effect=country_pool.PoolError('pool_unavailable')):
            with self.assertRaises(country_pool.PoolError):subscription_cslive_go._acquire_order_proxy(id,'PH',600)
        self.assertIsNone(db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1',id))
        self.assertIsNone(retry.load_checkout(db.one('SELECT * FROM subscription_runs WHERE order_id=$1',id),subscription_cslive_go.FLOW))
