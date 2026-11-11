"""Country allocation and adapter checks use disposable PG and fake providers."""
import json
import os
import subprocess
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from test_registration_account import RegistrationAccountTests as Fixture, ROOT
from backend import db, national_proxies as pool, country_pool, regions, subscription, subscription_cslive_go, subscription_cslive_full
from backend.common import encrypt, decrypt, BusinessError
from backend.seed import DEFAULTS


class NationalProxyTests(unittest.TestCase):
    tearDown = Fixture.tearDown

    def setUp(self):
        Fixture.setUp(self)
        for key, value in DEFAULTS.items():
            db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING', key, value)
        db.execute("UPDATE settings SET value=value||'{\"mode\":\"national_pool\"}'::jsonb WHERE key='proxy'")
        db.execute("INSERT INTO products(id,name,description,fee) VALUES('prod','Fixture','',100)")

    def order(self, id):
        db.execute("INSERT INTO orders(id,user_id,product_id,product_name,email,status,fee,idempotency_key,request_hash,workflow) VALUES($1,'u','prod','Fixture','account@fixture.invalid','processing',100,$1,'fixture','subscription')", id)

    def entry(self, id, country='PH', minutes=30):
        db.execute('INSERT INTO subscription_country_entries(country,id,endpoint_enc,session_minutes) VALUES($1,$2,$3,$4)', country, id, encrypt('http://username:original-password@'+id+'.invalid:8080'), minutes)

    def test_parallel_exclusive_and_repeated_order_reuses_exact_url(self):
        for i in range(20): self.entry('proxy'+str(i)); self.order('order'+str(i))
        def acquire(i): return subscription_cslive_go._acquire_order_proxy('order'+str(i), 'PH', 600)
        start=time.monotonic()
        with ThreadPoolExecutor(max_workers=20) as workers: results=list(workers.map(acquire,range(20)))
        self.assertEqual(len(set(results)),20)
        self.assertTrue(all('original-password@' in x and '_session' not in x for x in results))
        with ThreadPoolExecutor(max_workers=20) as workers: same=list(workers.map(lambda _:acquire(0),range(40)))
        self.assertEqual(set(same),{results[0]})
        self.assertEqual(db.scalar('SELECT count(*) FROM subscription_country_usage WHERE in_use'),20)
        self.order('overflow')
        with self.assertRaisesRegex(RuntimeError,'pool_capacity'):subscription._acquire_order_proxy('overflow','PH',600)
        print(f'Country inventory: 60 concurrent allocation/reuse requests, {time.monotonic()-start:.3f}s, duplicate allocations 0')

    def test_release_cooldown_fallback_and_paid_tail_protection(self):
        self.entry('one');self.entry('two');self.order('first')
        endpoint=subscription._acquire_order_proxy('first','PH',600)
        first=db.scalar("SELECT resource_id FROM order_proxy_sessions WHERE order_id='first'")
        db.execute("UPDATE orders SET status='succeeded',activation='pending',renewal='pending' WHERE id='first'")
        pool.reap();self.assertTrue(db.scalar('SELECT in_use FROM subscription_country_usage WHERE id=$1',first))
        db.execute("INSERT INTO subscription_followups(order_id,checkpoint_enc,phase) VALUES('first','fixture','completed')")
        pool.reap();row=db.one('SELECT * FROM subscription_country_usage WHERE id=$1',first)
        self.assertFalse(row['in_use']);self.assertGreater(row['cooldown_until'],row['released_at'])
        self.order('second');subscription_cslive_full._acquire_order_proxy('second','PH',600)
        self.assertNotEqual(db.scalar("SELECT resource_id FROM order_proxy_sessions WHERE order_id='second'"),first)
        self.order('third');self.assertEqual(subscription._acquire_order_proxy('third','PH',600),endpoint)
        # A stopped historical order cannot reclaim a proxy another task occupies.
        with self.assertRaisesRegex(RuntimeError,'pool_capacity'):pool.ensure_order('first')

    def test_recovery_lock_and_queued_retry_survive_reaping(self):
        self.entry('retained');self.order('retained')
        endpoint=subscription_cslive_go._acquire_order_proxy('retained','PH',600)
        db.execute("UPDATE orders SET status='queued' WHERE id='retained'")
        pool.reap();self.assertTrue(db.scalar('SELECT in_use FROM subscription_country_usage'))
        db.execute('DELETE FROM subscription_country_entries')
        self.assertEqual(subscription_cslive_go._acquire_order_proxy('retained','PH',600),endpoint)
        with db.transaction() as q:
            q.execute('SELECT pg_advisory_xact_lock(1886018)')
            # Catalog management must not block already bound orders.
            self.assertEqual(subscription_cslive_go._acquire_order_proxy('retained','PH',600),endpoint)
        db.execute("UPDATE orders SET status='review' WHERE id='retained'")
        with db.transaction() as q:
            q.execute("SELECT pg_advisory_xact_lock(1886015,hashtext('retained'))")
            pool.reap();self.assertTrue(db.scalar('SELECT in_use FROM subscription_country_usage'))
        pool.reap();self.assertFalse(db.scalar('SELECT bool_or(in_use) FROM subscription_country_usage'))

    def test_modes_regions_and_renewal_binding(self):
        self.entry('renew','GLOBAL')
        self.assertEqual(regions.enabled('cslive_go_direct_pay'),[])
        self.entry('country','PH')
        self.assertEqual([r['code'] for r in regions.enabled('cslive_go_direct_pay')],['PH'])
        db.execute("INSERT INTO renewal_tasks(id,user_id,email,account_hash,credentials_enc,credentials_expires_at) VALUES('renewal','u','r@fixture.invalid','hash','fixture',now()+interval '1 hour')")
        with db.transaction() as q: first=pool.acquire_renewal(q,'renewal')
        with db.transaction() as q: self.assertEqual(pool.acquire_renewal(q,'renewal'),first)
        with db.transaction() as q:
            with self.assertRaisesRegex(RuntimeError,'proxy_mode_changed'):country_pool.acquire_renewal(q,'renewal')
        with db.transaction() as q:
            with self.assertRaises(BusinessError):country_pool.change_mode(q,'manual')
        db.execute("UPDATE renewal_tasks SET status='disabled' WHERE id='renewal'")
        pool.reap();self.assertFalse(db.scalar('SELECT bool_or(in_use) FROM subscription_country_usage'))

    def test_original_provider_service_isolated_authenticated_and_published(self):
        env={**os.environ,'XUFU_INTEGRATION':'1','REGISTRATION_GATEWAY_SECRET':'fixture-gateway-secret-longer-than-32'}
        result=subprocess.run(['node','tests/national_proxy_service.cjs'],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=70)
        self.assertEqual(result.returncode,0,result.stderr[-2500:]+result.stdout[-1000:])
        entries=db.all('SELECT * FROM subscription_country_entries')
        self.assertEqual(len(entries),2)
        self.assertTrue(all('fixture.invalid' in decrypt(row['endpoint_enc']) for row in entries))
