"""Pre-payment checkout rejection retries, isolated PG and fake providers only."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_manual_proxy_failover as fixture
from backend import db, country_pool, national_proxies, subscription_cslive_go as flow
from backend import subscription_retry as retry
from backend.checkout_errors import CheckoutError
from backend.common import encrypt, now


class CheckoutRestartTests(unittest.TestCase):
    setUp = fixture.ManualProxyFailoverTests.setUp
    tearDown = fixture.ManualProxyFailoverTests.tearDown
    order = fixture.ManualProxyFailoverTests.order
    acquire = fixture.ManualProxyFailoverTests.acquire
    processing = fixture.ManualProxyFailoverTests.processing

    def test_manual_dynamic_retry_renews_session_without_cooling_healthy_resource(self):
        for vendor in ('iproyal', 'cliproxy', '1024proxy', '711proxy'):
            with self.subTest(vendor=vendor):
                db.execute("UPDATE resources SET vendor=$1,ip_mode='dynamic',session_minutes=15 WHERE id='main'", vendor)
                oid = self.order(vendor)
                first = self.acquire(oid, flow)
                before = db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1', oid)
                retry.save_checkout(oid, flow.FLOW, {'ok':True,'hostedUrl':'https://fixture.invalid/old','checkoutSessionId':'cs_old'}, {'cookies':'old-context'})
                funds = db.one("SELECT balance,frozen FROM users WHERE id='u'")
                self.assertTrue(retry.handle_failure(oid, CheckoutError('http_400')))
                saved = db.one('SELECT * FROM subscription_runs WHERE order_id=$1', oid)
                self.assertEqual((saved['checkout_restart_count'], saved['network_retry_count']), (1, 0))
                self.assertEqual((saved['checkout_checkpoint_enc'], saved['checkout_url_enc'], saved['checkout_session_id']), ('', '', ''))
                self.assertGreater((saved['network_retry_at']-now()).total_seconds(), 5)
                self.assertIsNone(db.one('SELECT * FROM proxy_leases WHERE id=$1', before['proxy_lease_id']))
                self.assertEqual(db.one("SELECT proxy_failures,cooldown_until FROM resources WHERE id='main'"), {'proxy_failures':0,'cooldown_until':None})
                self.processing(oid)
                second = self.acquire(oid, flow)
                self.assertNotEqual(first, second)
                self.assertEqual(self.acquire(oid, flow), second)
                self.assertNotEqual(db.scalar('SELECT session_ref FROM order_proxy_sessions WHERE order_id=$1', oid), before['session_ref'])
                self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"), funds)
                self.assertEqual(db.scalar('SELECT count(*) FROM ledger WHERE order_id=$1', oid), 0)

    def test_country_supplier_retry_rejects_retired_session(self):
        self.order()
        db.execute("UPDATE settings SET value=value||$1::jsonb WHERE key='proxy'", {'mode':'country_pool'})
        with db.transaction() as q:
            country_pool.write(q, dict(country_pool.DEFAULTS, token_enc=encrypt('fixture'), username='fixture',
                password_enc=encrypt('fixture'), countries=[{'code':'PH'}]))
        def generated(session):
            return [f'geo.iproyal.com:12321:fixture:fixture_country-ph_session-{session}_lifetime-1h']
        with patch.object(country_pool, 'api', return_value=generated('old00000')):
            first = self.acquire('order', flow)
        with patch.object(country_pool, 'api') as api:
            self.assertTrue(retry.handle_failure('order', CheckoutError('http_400')))
            api.assert_not_called()
        self.assertTrue(db.scalar("SELECT EXISTS(SELECT 1 FROM country_pool_sessions WHERE session_id='old00000')"))
        self.processing()
        with patch.object(country_pool, 'api', side_effect=[generated('old00000'), generated('fresh000')]) as api:
            second = self.acquire('order', flow)
            self.assertNotEqual(first, second)
            self.assertIn('fresh000', second)
            self.assertEqual(api.call_count, 2)

    def test_national_retry_releases_only_own_entry_and_takes_another(self):
        self.order(); self.order('other')
        db.execute("UPDATE settings SET value=value||$1::jsonb WHERE key='proxy'", {'mode':'national_pool'})
        for entry in ('old', 'new', 'occupied'):
            db.execute('INSERT INTO subscription_country_entries(country,id,endpoint_enc,session_minutes) VALUES($1,$2,$3,30)', 'PH', entry, encrypt('http://user:pass@'+entry+'.invalid:8080'))
        with db.transaction() as q:
            national_proxies.reserve(q, 'occupied', 'order', 'other', 30)
        first = self.acquire('order', flow)
        old = db.scalar("SELECT resource_id FROM order_proxy_sessions WHERE order_id='order'")
        self.assertTrue(retry.handle_failure('order', CheckoutError('http_400')))
        usage = db.one('SELECT * FROM subscription_country_usage WHERE id=$1', old)
        self.assertFalse(usage['in_use'])
        self.assertGreater(usage['cooldown_until'], now())
        self.assertTrue(db.scalar("SELECT in_use FROM subscription_country_usage WHERE id='occupied'"))
        self.processing()
        self.assertNotEqual(first, self.acquire('order', flow))

    def test_duplicate_failures_schedule_once_and_keep_other_orders_lease(self):
        self.order(); self.order('other'); self.acquire(); self.acquire('other')
        other = db.one("SELECT * FROM order_proxy_sessions WHERE order_id='other'")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: retry.handle_failure('order', CheckoutError('http_400')), range(8)))
        self.assertEqual(results, [True]*8)
        self.assertEqual(db.scalar("SELECT checkout_restart_count FROM subscription_runs WHERE order_id='order'"), 1)
        self.assertEqual(db.scalar("SELECT count(*) FROM order_events WHERE order_id='order' AND stage='checkout_retry'"), 1)
        self.assertEqual(db.one("SELECT * FROM order_proxy_sessions WHERE order_id='other'"), other)
        self.assertEqual(db.scalar('SELECT count(*) FROM proxy_leases'), 1)
        diagnostic = db.scalar("SELECT diagnostics FROM payment_trace_events WHERE order_id='order' ORDER BY id DESC LIMIT 1")
        self.assertNotIn('network_reason', diagnostic)
        self.assertFalse(diagnostic['checkout_reused'])

    def test_payment_fence_and_non_checkout_errors_never_restart(self):
        cases = ('paid','submitted','review','deleted','exhausted','disabled','untyped','unauthorized','forbidden','declined')
        for case in cases:
            with self.subTest(case=case):
                self.order(case); self.acquire(case)
                if case=='paid': db.execute('UPDATE subscription_runs SET paid_at=now() WHERE order_id=$1', case)
                if case=='submitted': retry.mark_payment_submission(case)
                if case=='review': db.execute("UPDATE orders SET status='review' WHERE id=$1", case)
                if case=='deleted': db.execute('UPDATE orders SET deleted_at=now() WHERE id=$1', case)
                if case=='exhausted': db.execute('UPDATE subscription_runs SET checkout_restart_count=2 WHERE order_id=$1', case)
                db.execute("UPDATE settings SET value=value||$1::jsonb WHERE key='payment'", {'checkout_restart_limit':0 if case=='disabled' else 2})
                exc = {'untyped':RuntimeError('http_400'), 'unauthorized':CheckoutError('http_401'),
                       'forbidden':CheckoutError('http_403'), 'declined':RuntimeError('payment_declined')}.get(case, CheckoutError('http_400'))
                before = db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1', case)
                count = db.scalar('SELECT checkout_restart_count FROM subscription_runs WHERE order_id=$1', case)
                retry.handle_failure(case, exc)
                self.assertEqual(db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1', case), before)
                self.assertEqual(db.scalar('SELECT checkout_restart_count FROM subscription_runs WHERE order_id=$1', case), count)
                if case=='submitted':
                    self.assertEqual(db.scalar('SELECT status FROM orders WHERE id=$1', case), 'review')
                    self.assertIsNotNone(db.scalar('SELECT payment_submit_started_at FROM subscription_runs WHERE order_id=$1', case))
