"""Isolated PostgreSQL checks: no workers, upstream calls or real funds."""
import csv
import io
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
from unittest.mock import patch
from starlette.requests import Request
from fastapi.testclient import TestClient
from test_dashboard import DashboardTests as Fixture
from backend import db, finance_reports as reports, public_metrics
from backend.common import BusinessError, digest


class FinanceReportTests(unittest.TestCase):
    setUp = Fixture.setUp
    tearDown = Fixture.tearDown
    sub = Fixture.sub
    reg = Fixture.reg
    ledger = Fixture.ledger

    def query(self, **values):
        return reports.filters(Request({'type':'http','method':'GET','path':'/','headers':[],
                                      'query_string':urlencode(values,doseq=True).encode()}))

    def refund(self, id='refund', owner='u', status='succeeded'):
        db.execute("INSERT INTO refunds(id,user_id,email,credentials,status,fee) VALUES($1,$2,$1||'@test.example','test',$3,500)", id,owner,status)

    def test_unified_actual_money_and_order_progress(self):
        self.sub('s',renewal='failed');self.reg('r',status='partial',delivered=2);self.refund()
        self.sub('admin-s','admin');self.ledger('admin','charge',-900,owner='admin')
        for id,kind,amount,business in [('in','redeem',1000,'registration'),('add','adjustment',200,'subscription'),
            ('minus','adjustment',-50,'subscription'),('s','charge',-500,'subscription'),('sr','refund',100,'subscription'),
            ('r','charge',-300,'registration'),('f','charge',-400,'refund'),('reward','invite_reward',70,'subscription')]:
            self.ledger(id,kind,amount,business)
        result=reports.accounts_data(self.query())
        totals=result['summary']
        self.assertEqual((totals['customers'],totals['redeemed'],totals['added'],totals['debited'],totals['used'],totals['rewarded']), (2,1000,200,50,1100,70))
        self.assertEqual((totals['applications'],totals['succeeded'],totals['partial'],totals['delivered']),(3,2,1,2))
        self.assertEqual(totals['available'],700)
        self.assertEqual([r['business'] for r in result['businesses']],['registration','subscription','refund'])
        self.assertEqual([r['used'] for r in result['businesses']],[300,400,400])
        self.assertEqual(sum(r['used'] for r in result['trend']),1100)
        self.assertEqual(reports.ledger_data(self.query(),business='shared')['total'],4)
        self.assertEqual(reports.accounts_data(self.query(include_admin='true'))['summary']['used'],2000)
        self.assertEqual(reports.accounts_data(self.query(exclude_user_id=['u']))['summary']['applications'],0)

    def test_cross_midnight_holds_deleted_history_and_negative_net(self):
        self.ledger('old','freeze',0,created_at='2026-11-10T15:59:59Z')
        self.ledger('release','unfreeze',0,created_at='2026-11-10T16:00:00Z')
        db.execute("UPDATE ledger SET frozen_after=0 WHERE reference='release'")
        self.ledger('refund','refund',101,created_at='2026-11-10T16:00:01Z')
        self.sub('s',created_at='2026-11-11T15:59:59Z',deleted_at='2026-11-11T17:00:00Z')
        db.execute("UPDATE users SET deleted_at=now(),status='disabled' WHERE id='u'")
        data=reports.accounts_data(self.query(date='2026-11-11',status='deleted'))
        self.assertEqual((data['summary']['released'],data['summary']['freeze_added'],data['summary']['used'],data['summary']['applications']),(300,0,-101,1))
        entries=reports.ledger_data(self.query(date='2026-11-11'),kind='unfreeze')['items']
        self.assertEqual((entries[0]['frozen_delta'],entries[0]['available_delta']),(-300,300))

    def test_real_pages_summary_and_export(self):
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code,balance) SELECT 'load-'||n,'load-'||n||'@example.test','Load '||n,'unused','load-'||n,n FROM generate_series(1,57)n")
        f=self.query(q='load-')
        first=reports.accounts_data(f,sort_by='available',sort_dir='asc')
        second=reports.accounts_data(f,page=2,sort_by='available',sort_dir='asc')
        self.assertEqual((first['total'],len(first['items']),second['items'][0]['balance']),(57,10,11))
        self.assertEqual(first['summary'],second['summary'])
        self.assertEqual(first['summary']['balance'],1653)
        for size in (10,50,100,500):self.assertEqual(len(reports.accounts_data(f,page_size=size)['items']),min(size,57))
        self.assertEqual(len(reports.accounts_data(f,exporting=True)['items']),57)
        with self.assertRaises(BusinessError):reports.accounts_data(f,page_size=25)
        with self.assertRaises(BusinessError):reports.accounts_data(f,sort_by='id;drop table users')
        db.execute("UPDATE users SET name='=unsafe()' WHERE id='load-1'")
        request=Request({'type':'http','method':'GET','path':'/','headers':[],'query_string':b'q=load-1'})
        result=reports.export(request)
        rows=list(csv.reader(io.StringIO(result.body.decode('utf-8-sig'))))
        self.assertTrue(any("'=unsafe()" in row for row in rows))
        self.assertTrue(any('0.01' in row for row in rows))

    def test_admin_permissions_and_public_aggregate_contract(self):
        from backend.main import app
        self.sub('s');self.sub('admin-s','admin',status='failed');self.reg('r','pa',quantity=20,delivered=20);self.refund()
        self.sub('link');db.execute("UPDATE orders SET workflow='link' WHERE id='link'")
        self.sub('deleted',deleted_at='2026-11-12T00:00:00Z')
        public_metrics._expires=0;public_metrics._cached=None
        for id in ('u','admin'):db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')",digest('fixture-'+id),id)
        anonymous=TestClient(app);member=TestClient(app,cookies={'xupay_session':'fixture-u'});admin=TestClient(app,cookies={'xupay_session':'fixture-admin'})
        try:
            for path in ('','/ledger','/options','/export'):
                self.assertEqual(anonymous.get('/api/admin/finance/reports'+path).status_code,401)
                self.assertEqual(member.get('/api/admin/finance/reports'+path).status_code,403)
                result=admin.get('/api/admin/finance/reports'+path)
                self.assertEqual(result.status_code,200,result.text)
            result=anonymous.get('/api/public/order-counts')
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(set(result.json()),{'total','registration','subscription','refund','updated_at'})
            self.assertEqual({k:v for k,v in result.json().items() if k!='updated_at'},dict(total=5,registration=1,subscription=3,refund=1))
        finally:anonymous.close();member.close();admin.close()

    def test_count_cache_single_refresh_and_short_failure_backoff(self):
        self.sub('s');public_metrics._expires=0;public_metrics._cached=None
        real=db.transaction
        with patch.object(public_metrics.db,'transaction',wraps=real) as spy:
            with ThreadPoolExecutor(max_workers=40) as pool:results=list(pool.map(lambda _:public_metrics.counts(),range(100)))
            self.assertEqual(spy.call_count,1)
            self.assertTrue(all(r['total']==1 for r in results))
        public_metrics._expires=0
        with patch.object(public_metrics.db,'transaction',side_effect=RuntimeError('synthetic')) as spy:
            for _ in range(2):
                with self.assertRaises(BusinessError):public_metrics.counts()
            self.assertEqual(spy.call_count,1)
        public_metrics._expires=0;public_metrics._cached=None

    def test_account_cards_follow_filters_and_effective_membership(self):
        from backend.admin_accounts import users
        from backend.membership import assign_membership
        from backend.seed import DEFAULTS
        for key,value in DEFAULTS.items():
            db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        self.ledger('charged','charge',-500);self.ledger('returned','refund',100)
        db.execute("UPDATE users SET status='disabled' WHERE id='v'")
        result=users()
        self.assertEqual((result['summary']['total'],result['summary']['active'],result['summary']['disabled'],result['summary']['used_balance']),(3,2,1,400))
        self.assertEqual(result['summary']['available'],700)
        tier=result['tiers'][1]['code']
        with db.transaction() as tx:assign_membership(tx,['u'],tier,None)
        upgraded=users(tier=tier)
        self.assertEqual((upgraded['total'],upgraded['summary']['upgraded'],upgraded['summary']['used_balance']),(1,1,400))
        self.assertEqual(users(status='disabled')['summary']['used_balance'],0)
        db.execute("UPDATE users SET membership_until=now()-interval '1 hour' WHERE id='u'")
        self.assertEqual(users()['summary']['upgraded'],0)

    def test_finance_concurrent_reads_do_not_mutate_money(self):
        db.execute("INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,description) SELECT 'u','charge',-1,1000,0,'load-'||n,'synthetic' FROM generate_series(1,5000)n")
        before=db.all('SELECT id,balance,frozen FROM users ORDER BY id')
        f=self.query()
        def read(_):
            start=time.perf_counter();self.assertEqual(reports.accounts_data(f)['summary']['used'],5000);return time.perf_counter()-start
        with ThreadPoolExecutor(max_workers=20) as pool:times=sorted(pool.map(read,range(40)))
        self.assertEqual(before,db.all('SELECT id,balance,frozen FROM users ORDER BY id'))
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),5000)
        print('Finance 40 reads / 20 concurrent: p95=%.3fs max=%.3fs'%(times[37],times[-1]))


del Fixture
