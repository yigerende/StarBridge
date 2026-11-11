"""Isolated PG allocation/retry tests; no supplier or payment requests."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx
import test_registration_account as fixture
from backend import db, manual_proxies as manual, subscription, subscription_cslive_go, subscription_cslive_full
from backend import subscription_retry as retry, proxies, settings
from backend.checkout_errors import CheckoutError
from backend.common import encrypt, decrypt, now, BusinessError
from backend.country_pool import PoolError
from backend.seed import DEFAULTS


class ManualProxyFailoverTests(unittest.TestCase):
    tearDown = fixture.RegistrationAccountTests.tearDown

    def setUp(self):
        fixture.RegistrationAccountTests.setUp(self)
        for key, value in DEFAULTS.items():
            db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        db.execute("INSERT INTO products(id,name,description,fee) VALUES('plus','Plus','fixture',300)")
        for id, priority, country, vendor in [('main',0,'PH','711proxy'),('backup1',1,'PH','1024proxy'),('backup2',2,'PH','other'),('foreign',0,'US','other')]:
            db.execute("INSERT INTO resources(id,name,country,endpoint_enc,vendor,ip_mode,capacity,failover_priority) VALUES($1,$1,$2,$3,$4,'static',100,$5)",id,country,encrypt(f'http://user:password@{id}.example.test:8080'),vendor,priority)

    def order(self, id='order'):
        db.execute("INSERT INTO orders(id,user_id,product_id,product_name,email,country,status,fee,idempotency_key,request_hash,workflow) VALUES($1,'u','plus','Plus','test@example.test','PH','processing',300,$1,'fixture','subscription')",id)
        db.execute("INSERT INTO subscription_runs(order_id,user_id,subscription_flow,billing_snapshot) VALUES($1,'u','gener_direct_pay',$2)",id,{'country':'US','city':'fixture'})
        return id

    def acquire(self, id='order', module=subscription):
        return module._acquire_order_proxy(id,'PH',600)

    def processing(self,id='order'):
        db.execute("UPDATE orders SET status='processing' WHERE id=$1",id)

    def test_vendor_roundtrip_priority_and_cooldown_validation(self):
        self.request.state.user['role']='admin'
        for vendor in ('1024proxy','711proxy'):
            value=dict(name=vendor,country='PH',endpoint='http://user:pass@vendor.example.test:8080',enabled=True,weight=1,capacity=2,vendor=vendor,ip_mode='dynamic',failover_priority=2)
            id=proxies.save_resource_value(value,self.request)['id']
            resource=next(r for r in proxies.resources()['items'] if r['id']==id)
            self.assertEqual((resource['vendor'],resource['failover_priority']),(vendor,2))
            url,lifetime=subscription._proxy_url_with_session(db.one('SELECT * FROM resources WHERE id=$1',id),'PH')
            self.assertEqual(url,value['endpoint'])
        for priority in (-1,101,True):
            with self.assertRaises(BusinessError):proxies.save_resource_value(dict(value,failover_priority=priority),self.request)

    def test_three_flows_share_allocation_and_keep_bound_route(self):
        for index,module in enumerate((subscription,subscription_cslive_go,subscription_cslive_full)):
            id=self.order(str(index));url=self.acquire(id,module)
            self.assertIn('@main.',url)
            db.execute("UPDATE resources SET enabled=false WHERE id='main'")
            self.assertEqual(self.acquire(id,module),url)
            db.execute("UPDATE resources SET enabled=true WHERE id='main'")
        self.assertEqual(db.scalar('SELECT count(*) FROM proxy_leases'),3)

    def test_network_retry_uses_backup_clears_checkout_only_and_releases_own_lease(self):
        self.order();self.order('other');self.acquire();self.acquire('other')
        retry.save_checkout('order',subscription.FLOW,{'ok':True,'hostedUrl':'https://fixture.test/cs','checkoutSessionId':'cs'},{'cookie':'fixture'})
        funds=db.one("SELECT balance,frozen FROM users WHERE id='u'")
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(lambda _:retry.handle_failure('order',CheckoutError('network_error')),range(8))),[True]*8)
        self.assertEqual(db.scalar('SELECT count(*) FROM proxy_leases'),1)
        self.assertEqual(db.scalar("SELECT proxy_failures FROM resources WHERE id='main'"),1)
        self.assertEqual(db.scalar("SELECT network_retry_count FROM subscription_runs WHERE order_id='order'"),1)
        self.assertEqual(db.scalar("SELECT checkout_checkpoint_enc FROM subscription_runs WHERE order_id='order'"),'')
        self.assertGreater(db.scalar("SELECT extract(epoch FROM cooldown_until-now()) FROM resources WHERE id='main'"),280)
        self.processing();self.assertIn('@backup1.',self.acquire())
        self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),funds)
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)
        retry.handle_failure('order',httpx.ConnectError('connection refused'))
        self.processing();self.assertIn('@backup2.',self.acquire())

    def test_non_network_errors_keep_binding_and_health(self):
        errors=[CheckoutError(x) for x in ('http_401','http_403','http_429','http_500','device_id_missing','sentinel_challenge_missing')]
        errors += [RuntimeError('payment_declined'),httpx.ProxyError('407 Proxy Authentication Required'),httpx.PoolTimeout('timeout'),RuntimeError('certificate verify failed')]
        for index,exc in enumerate(errors):
            id=self.order(str(index));self.acquire(id);binding=db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1',id)
            self.assertFalse(manual.network_failure(exc),str(exc))
            retry.handle_failure(id,exc)
            self.assertEqual(db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1',id),binding)
        self.assertEqual(db.scalar('SELECT sum(proxy_failures) FROM resources'),0)

    def test_payment_fence_retains_original_trade_and_does_not_change_wallet(self):
        for id in ('submitted','paid','full'):
            self.order(id);self.acquire(id)
            retry.save_checkout(id,subscription.FLOW,{'ok':True,'hostedUrl':'https://fixture.test/cs','checkoutSessionId':'cs'})
            retry.mark_payment_submission(id)
            if id=='paid':db.execute('UPDATE subscription_runs SET paid_at=now() WHERE order_id=$1',id)
            if id=='full':db.execute("UPDATE subscription_runs SET subscription_flow='cslive_full_go' WHERE order_id=$1",id)
            before=db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1',id)
            self.assertTrue(retry.handle_failure(id,CheckoutError('network_error')))
            self.assertEqual(db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1',id),before)
            self.assertIsNotNone(db.scalar('SELECT payment_submit_started_at FROM subscription_runs WHERE order_id=$1',id))
            self.assertEqual(db.scalar('SELECT network_retry_count FROM subscription_runs WHERE order_id=$1',id),0)
            self.assertEqual(db.scalar('SELECT checkout_session_id FROM subscription_runs WHERE order_id=$1',id),'cs')
        self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),dict(balance=1000,frozen=300))
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)

    def test_capacity_does_not_activate_backup_all_cooling_wait_without_retry_charge(self):
        db.execute("UPDATE resources SET capacity=1 WHERE id='main'")
        self.order();self.acquire();self.order('waiting')
        with self.assertRaises(PoolError):self.acquire('waiting')
        db.execute("UPDATE resources SET proxy_failures=1,cooldown_until=now()+interval '5 minutes'")
        with self.assertRaises(PoolError) as exc:self.acquire('waiting')
        retry.handle_failure('waiting',exc.exception)
        self.assertEqual(db.scalar("SELECT network_retry_count FROM subscription_runs WHERE order_id='waiting'"),0)
        self.assertEqual(db.scalar("SELECT status FROM orders WHERE id='waiting'"),'queued')
        self.assertEqual(db.scalar('SELECT count(*) FROM order_proxy_sessions'),1)

    def test_concurrent_half_open_allows_one_probe_then_restores_primary(self):
        db.execute("UPDATE resources SET proxy_failures=1,cooldown_until=now()-interval '1 second' WHERE id='main'")
        for i in range(50):self.order(str(i))
        with ThreadPoolExecutor(max_workers=20) as pool:urls=list(pool.map(lambda i:self.acquire(str(i)),range(50)))
        self.assertEqual(sum('@main.' in url for url in urls),1)
        self.assertEqual(sum('@backup1.' in url for url in urls),49)
        probe=db.scalar("SELECT probe_owner FROM resources WHERE id='main'")
        manual.succeeded(probe)
        self.order('next');self.assertIn('@main.',self.acquire('next'))
        self.assertEqual(db.scalar("SELECT proxy_failures FROM resources WHERE id='main'"),0)

    def test_stale_success_does_not_clear_fault_and_edit_isolates_old_failures(self):
        self.order('old');self.acquire('old');self.order();self.acquire()
        retry.handle_failure('order',CheckoutError('network_error'));manual.succeeded('old')
        self.assertEqual(db.scalar("SELECT proxy_failures FROM resources WHERE id='main'"),1)
        self.request.state.user['role']='admin'
        proxies.save_resource_value(dict(name='fixed',country='PH',endpoint='http://new:pass@fixed.example.test:8080',enabled=True,weight=1,capacity=100,vendor='711proxy',ip_mode='static'),self.request,'main')
        retry.handle_failure('old',CheckoutError('network_error'))
        self.assertEqual(db.scalar("SELECT proxy_failures FROM resources WHERE id='main'"),0)
        self.order('next');self.assertIn('@fixed.',self.acquire('next'))

    def test_retry_prefers_untried_backup_even_when_cooldown_elapsed(self):
        self.order();self.acquire();retry.handle_failure('order',CheckoutError('network_error'))
        db.execute("UPDATE resources SET cooldown_until=now()-interval '1 second' WHERE id='main'")
        self.processing();self.assertIn('@backup1.',self.acquire())
        self.order('new');self.assertIn('@main.',self.acquire('new'))

    def test_old_settings_omit_new_optional_value_without_reset(self):
        self.request.state.user['role']='admin'
        value=dict(DEFAULTS['proxy'],failure_cooldown_seconds=120)
        settings.save_settings_value('proxy',value,self.request)
        value=dict(value);value.pop('failure_cooldown_seconds')
        settings.save_settings_value('proxy',value,self.request)
        self.assertEqual(settings.settings()['proxy']['failure_cooldown_seconds'],120)
        for seconds in (0,3601,True):
            with self.assertRaises(BusinessError):settings.validate_config('proxy',dict(value,failure_cooldown_seconds=seconds))
