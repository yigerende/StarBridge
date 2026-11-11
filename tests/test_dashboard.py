"""Unified dashboard behavior, isolated PostgreSQL; never starts execution workers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import time
import unittest
from unittest.mock import Mock, patch
import importlib.util
from pathlib import Path

from test_registration_account import RegistrationAccountTests as Fixture
from backend import db
from backend.common import BusinessError, digest
from backend.dashboard import overview, shared_overview
from backend.report_period import ZONE


class DashboardTests(unittest.TestCase):
    tearDown = Fixture.tearDown

    def setUp(self):
        Fixture.setUp(self)
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code,role) VALUES('admin','admin@test.example','Admin','synthetic','admin','admin')")
        db.execute("INSERT INTO platform_users(id,email,name,password_hash,xufu_user_id) VALUES('pv','v@reg.example','Other','unused','v'),('pa','admin@reg.example','Admin','unused','admin')")
        db.execute("INSERT INTO products(id,name,description,fee) VALUES('plus','Plus','test',500)")
        self.user = {'id':'u','role':'member'}
        self.admin_user = {'id':'admin','role':'admin'}

    def test_update_waits_for_active_execution_but_preserves_inactive_review(self):
        spec=importlib.util.spec_from_file_location('updater_dashboard_test',Path(__file__).resolve().parents[1]/'scripts/starbridge_updater.py')
        updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)
        service=updater.Updater();service.publish=Mock();service.engine_idle=Mock(return_value=True)
        service.sql=lambda query: 't' if 'to_regclass' in query else str(db.scalar(query))
        self.sub('review-for-update',status='review')
        before=db.one("SELECT * FROM orders WHERE id='review-for-update'")
        service.wait_idle()
        for lock in ('pg_advisory_xact_lock(1886015,42)', 'pg_advisory_xact_lock(1886018)'):
            with db.transaction() as q:
                q.execute('SELECT '+lock)
                with patch.object(updater.time,'monotonic',side_effect=[0,0,601]),patch.object(updater.time,'sleep'):
                    with self.assertRaises(updater.UpdateError):service.wait_idle()
        db.execute("UPDATE orders SET status='processing' WHERE id='review-for-update'")
        with patch.object(updater.time,'monotonic',side_effect=[0,0,601]),patch.object(updater.time,'sleep'):
            with self.assertRaises(updater.UpdateError):service.wait_idle()
        db.execute("UPDATE orders SET status='review' WHERE id='review-for-update'")
        self.assertEqual(db.one("SELECT * FROM orders WHERE id='review-for-update'"),before)

    def sub(self, id, owner='u', status='succeeded', **fields):
        db.execute("INSERT INTO orders(id,user_id,product_id,product_name,email,status,fee,idempotency_key,request_hash,workflow,activation,renewal) VALUES($1,$2,'plus','Plus','subscription@test.example',$3,500,$1,'test','subscription','active','disabled')",id,owner,status)
        for key, value in fields.items():
            self.assertIn(key, ('activation','renewal','created_at','deleted_at'))
            db.execute('UPDATE orders SET '+key+'=$2 WHERE id=$1',id,value)

    def reg(self, id, owner='p', status='completed', quantity=3, delivered=3):
        db.execute("INSERT INTO platform_orders(id,user_id,quantity,delivered,need_oauth,unit_price,status,source,request_key,request_hash,template,attempt_limit,timeout_minutes) VALUES($1,$2,$3,$4,false,5,$5,'web',$1,'test','{}',1,20)",id,owner,quantity,delivered,status)

    def account(self, id, order, hours=0, owner='p'):
        db.execute("INSERT INTO platform_items(id,order_id,position,status) VALUES($1,$2,$3,'delivered')",id,order,int(id[-1]))
        db.execute("INSERT INTO platform_accounts(id,user_id,order_id,item_id,asset_id,email,need_oauth,credentials,created_at) VALUES($1,$2,$3,$1,$1,$1||'@test.example',false,'synthetic-secret',now()-$4*interval '1 hour')",id,owner,order,hours)

    def ledger(self, id, kind, amount, business='subscription', owner='u', created_at=None):
        db.execute("INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,description,business,created_at) VALUES($1,$2,$3,1000,300,$4,'test',$5,coalesce($6,now()))",owner,kind,amount,id,business,created_at)

    def test_customer_isolation_expiry_and_partial_delivery(self):
        self.sub('s');self.sub('hidden','v');self.reg('r',status='partial',delivered=2);self.reg('other','pv')
        self.account('a1','r',60);self.account('a2','r',1);self.account('a3','r',73)
        result=overview(self.user)
        self.assertEqual({r['id'] for r in result['recent_orders']},{'s','r'})
        self.assertEqual(result['wallet'],{'available':700,'frozen':300})
        todos={r['key']:r for r in result['attention']}
        self.assertEqual(todos['accounts']['count'],2)
        self.assertEqual(todos['expiring']['count'],1)
        self.assertIn('order_id=r',todos['expiring']['href'])
        registration=next(r for r in result['recent_orders'] if r['business']=='registration')
        self.assertEqual(registration['delivered'],2)
        self.assertEqual(registration['quantity'],3)
        self.assertIn('/accounts?business=registration&order_id=r',registration['claim_href'])
        self.assertNotIn('owner',registration)
        self.assertNotIn('credentials',json.dumps(result,default=str))
        self.assertNotIn('synthetic-secret',json.dumps(result,default=str))

    def test_admin_counts_both_businesses_and_net_settlement(self):
        self.sub('s');self.reg('r');self.sub('admin-s','admin');self.reg('admin-r','pa')
        self.ledger('s-fee','charge',-500);self.ledger('r-fee','charge',-700,'registration')
        self.ledger('s-refund','refund',100);self.ledger('admin-fee','charge',-900,owner='admin')
        for kind in ('topup','redeem','adjustment','freeze','unfreeze','invite_reward'):
            self.ledger(kind,kind,10000)
        result=overview(self.admin_user,admin=True)
        self.assertEqual(result['totals']['orders'],2)
        self.assertEqual(result['totals']['completed'],2)
        self.assertEqual(result['totals']['settled'],1100)
        self.assertEqual(sum(r['subscription_fees']+r['registration_fees'] for r in result['trend']),1100)
        included=overview(self.admin_user,admin=True,include_admin=True)
        self.assertEqual(included['totals']['orders'],4)
        self.assertEqual(included['totals']['settled'],2000)

    def test_current_attention_survives_historical_date_filter(self):
        self.sub('unknown',status='review');self.sub('paid',activation='review');self.sub('finishing',activation='pending')
        self.reg('reg-review',status='review',delivered=0)
        db.execute("INSERT INTO risk_events(category,severity,ip,detail,created_at) VALUES('login_failed','high','127.0.0.1','test',now()-interval '400 days')")
        result=overview(self.admin_user,admin=True,start='2020-01-01',end='2020-01-01',focus='attention')
        self.assertEqual(result['totals']['orders'],0)
        self.assertEqual(result['totals']['attention'],4)
        self.assertEqual({r['id'] for r in result['recent_orders']},{'unknown','paid','reg-review'})
        self.assertEqual(result['queues']['subscription']['settling'],1)
        self.assertEqual(result['queues']['subscription']['review'],1)
        self.assertEqual(result['queues']['subscription']['followup'],1)
        self.assertEqual(result['queues']['subscription']['running'],0)
        self.assertTrue(result['active'])
        self.assertIn('include_admin=false',result['attention'][0]['href'])

    def test_beijing_day_boundaries_and_delivered_unit(self):
        self.sub('before',created_at='2026-11-08T15:59:59Z')
        self.sub('inside',created_at='2026-11-08T16:00:00Z')
        self.sub('after',created_at='2026-11-09T16:00:00Z')
        self.reg('old',status='partial',delivered=2)
        db.execute("UPDATE platform_orders SET created_at='2026-11-01T00:00:00Z' WHERE id='old'")
        self.account('a1','old');self.account('a2','old')
        db.execute("UPDATE platform_accounts SET created_at='2026-11-09T02:00:00Z'")
        self.ledger('before','charge',-100,created_at='2026-11-08T15:59:59Z')
        self.ledger('inside','charge',-200,created_at='2026-11-08T16:00:00Z')
        self.ledger('after','charge',-300,created_at='2026-11-09T16:00:00Z')
        result=overview(self.admin_user,admin=True,start='2026-11-09',end='2026-11-09')
        self.assertEqual(result['totals']['orders'],1)
        self.assertEqual(result['totals']['settled'],200)
        self.assertEqual(result['queues']['registration']['delivered'],2)
        self.assertEqual(len(result['trend']),1)
        with self.assertRaises(BusinessError):overview(self.admin_user,admin=True,start='invalid')
        with self.assertRaises(BusinessError):overview(self.admin_user,admin=True,start='2020-01-01',end='2026-01-01')

    def test_closed_features_do_not_leak_account_data(self):
        self.sub('s');self.reg('r');self.account('a1','r')
        db.execute("INSERT INTO settings(key,value) VALUES('features',$1)",{'orders':False,'wallet':False,'accounts':False,'register':False})
        result=overview(self.user)
        self.assertIsNone(result['wallet'])
        self.assertEqual(result['recent_orders'],[])
        self.assertFalse(result['permissions']['register'])
        self.assertFalse(any(r['count'] for r in result['attention']))

    def test_server_permissions_and_filtered_destinations(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        self.sub('review',status='review');self.sub('paid',activation='review');self.sub('old-pending',status='queued');self.sub('admin-review','admin',status='review')
        db.execute("INSERT INTO risk_events(category,severity,ip,detail,created_at) VALUES('login_failed','high','127.0.0.1','old-risk',now()-interval '400 days')")
        for id in ('u','admin'):
            db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')",digest('fixture-'+id),id)
        with_test_user=TestClient(app,cookies={'xupay_session':'fixture-u'})
        admin_client=TestClient(app,cookies={'xupay_session':'fixture-admin'})
        anonymous=TestClient(app)
        try:
            self.assertEqual(anonymous.get('/api/overview').status_code,401)
            self.assertEqual(with_test_user.get('/api/admin/overview').status_code,403)
            r=with_test_user.get('/api/orders?attention=followup')
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual([x['id'] for x in r.json()['items']],['paid'])
            r=with_test_user.get('/api/orders?attention=pending')
            self.assertEqual([x['id'] for x in r.json()['items']],['old-pending'])
            self.assertEqual(admin_client.get('/api/admin/overview?days=2').status_code,400)
            self.assertEqual(admin_client.get('/api/admin/overview').status_code,200)
            r=admin_client.get('/api/admin/orders?status=review&include_admin=false')
            self.assertEqual([x['id'] for x in r.json()['items']],['review'])
            r=admin_client.get('/api/admin/risk?state=pending&all_time=true')
            self.assertEqual(r.status_code,200,r.text)
            self.assertTrue(any(x['detail']=='old-risk' for x in r.json()['items']))
        finally:
            with_test_user.close();admin_client.close();anonymous.close()

    def test_concurrent_reads_are_bounded_and_do_not_mutate_wallets(self):
        self.sub('s');self.reg('r');self.account('a1','r')
        # A nonempty history catches accidental per-order or upstream reads.
        db.execute("INSERT INTO orders(id,user_id,product_id,product_name,email,status,fee,idempotency_key,request_hash,workflow) SELECT 'load-'||n,'u','plus','Plus','test@example.test','queued',500,'load-'||n,'test','subscription' FROM generate_series(1,3000) n")
        before=db.all('SELECT id,balance,frozen FROM users ORDER BY id')
        def read(i):
            started=time.perf_counter();result=shared_overview(self.admin_user,admin=True) if i%2 else shared_overview(self.user)
            self.assertLessEqual(len(result['recent_orders']),8)
            return time.perf_counter()-started
        with ThreadPoolExecutor(max_workers=50) as pool:times=list(pool.map(read,range(100)))
        self.assertEqual(db.all('SELECT id,balance,frozen FROM users ORDER BY id'),before)
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)
        times.sort()
        print(f' dashboard DB: 100 reads / 50 concurrency / 3002 orders / two authorized scopes; errors=0 p50={times[49]:.3f}s p95={times[94]:.3f}s max={times[-1]:.3f}s')

    def test_simultaneous_customers_never_share_another_accounts_snapshot(self):
        self.sub('mine');self.sub('theirs','v');self.reg('mine-reg');self.reg('theirs-reg','pv')
        def read(i):
            owner='u' if i%2 else 'v'
            result=shared_overview({'id':owner,'role':'member'})
            self.assertEqual({r['id'] for r in result['recent_orders']}, {'mine','mine-reg'} if owner=='u' else {'theirs','theirs-reg'})
        with ThreadPoolExecutor(max_workers=30) as pool:list(pool.map(read,range(60)))
        # A later read must observe new records immediately; there is no TTL cache.
        self.sub('later')
        self.assertIn('later',{r['id'] for r in shared_overview(self.user)['recent_orders']})

    def test_fifty_distinct_customers_load_their_own_dashboards(self):
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code) SELECT 'customer-'||n,'customer-'||n||'@test.example','Customer','synthetic','customer-'||n FROM generate_series(0,49) n")
        db.execute("INSERT INTO orders(id,user_id,product_id,product_name,email,status,fee,idempotency_key,request_hash,workflow) SELECT 'order-'||n,'customer-'||n,'plus','Plus','test@example.test','queued',500,'order-'||n,'test','subscription' FROM generate_series(0,49) n")
        def read(i):
            started=time.perf_counter();n=i%50
            result=shared_overview({'id':'customer-'+str(n),'role':'member'})
            self.assertEqual([r['id'] for r in result['recent_orders']],['order-'+str(n)])
            return time.perf_counter()-started
        with ThreadPoolExecutor(max_workers=50) as pool:times=sorted(pool.map(read,range(100)))
        print(f' dashboard DB: 50 distinct customers / 100 reads / 50 concurrency; errors=0 p50={times[49]:.3f}s p95={times[94]:.3f}s max={times[-1]:.3f}s')


if __name__=='__main__':unittest.main()
