import unittest
import time
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
import test_dashboard as fixture
from backend import db
from backend.activity import listing
from backend.common import digest
from backend.main import app
from backend.seed import DEFAULTS


class ActivityTests(unittest.TestCase):
    setUp=fixture.DashboardTests.setUp
    tearDown=fixture.DashboardTests.tearDown
    sub=fixture.DashboardTests.sub
    reg=fixture.DashboardTests.reg

    def test_all_history_pagination_filters_and_customer_scope(self):
        for n in range(32):self.sub('subscription-'+str(n),status='queued' if n<10 else 'succeeded')
        self.sub('foreign','v');self.sub('admin-only','admin');self.sub('deleted',deleted_at='2026-11-01T00:00:00Z')
        self.reg('register-partial',status='partial',delivered=2)
        db.execute("INSERT INTO refunds(id,user_id,email,credentials,status,fee,fee_status) VALUES('refund-ok','u','customer@test.example','synthetic','succeeded',300,'settled')")
        first=listing(self.user,page=1,page_size=25);second=listing(self.user,page=2,page_size=25)
        self.assertEqual(first['total'],34);self.assertEqual(len(first['items']),25);self.assertEqual(len(second['items']),9)
        self.assertEqual(len({(r['business'],r['id']) for r in first['items']+second['items']}),34)
        self.assertEqual(first['summary']['active'],10);self.assertEqual(first['summary']['partial'],1)
        for row in first['items']+second['items']:
            self.assertNotIn(row['id'],('foreign','admin-only','deleted'));self.assertNotIn('owner',row);self.assertNotIn('user_id',row)
        self.assertEqual(listing(self.admin_user,admin=True)['total'],36)
        self.assertEqual(listing(self.admin_user,admin=True,include_admin=False)['total'],35)
        self.assertEqual(listing(self.user,business='registration')['total'],1)
        self.assertEqual(listing(self.user,state='active')['total'],10)
        self.assertEqual(listing(self.user,search='%')['total'],0)
        self.assertEqual(listing(self.user,search='foreign')['total'],0)
        self.assertEqual(listing(self.user,start='2000-01-01',end='2000-01-02')['total'],0)

    def test_admin_current_stage_from_execution_without_customer_exposure(self):
        from backend.order_stage import attach
        from backend.records import admin_orders
        self.sub('sub-running',status='processing')
        self.sub('sub-followup',status='succeeded',activation='active',renewal='failed')
        self.sub('sub-failed',status='failed')
        db.execute("INSERT INTO payment_trace_events(order_id,phase,state,code) VALUES('sub-running','confirming','running','request_started')")
        self.reg('reg-running',status='running',delivered=0)
        db.execute("INSERT INTO platform_items(id,order_id,position,status,stage) VALUES('reg-child','reg-running',1,'running','oauth')")
        db.execute("INSERT INTO refunds(id,user_id,email,credentials,status,fee) VALUES('refund-running','u','customer@test.example','synthetic','processing',300)")
        db.execute("INSERT INTO refund_proxies(id,name,secret) VALUES('proxy','fixture','synthetic')")
        db.execute("INSERT INTO refund_mothers(id,name,email,team_id,secret,proxy_id) VALUES('mother','fixture','mother@test.example','fixture-team','synthetic','proxy')")
        db.execute("INSERT INTO refund_execution(refund_id,mother_id,step) VALUES('refund-running','mother','removing')")
        stages={r['id']:r['current_stage'] for r in listing(self.admin_user,admin=True)['items']}
        self.assertEqual(stages['sub-running'],'提交付款')
        self.assertEqual(stages['sub-followup'],'取消续费待处理')
        self.assertEqual(stages['sub-failed'],'已结束（失败）')
        self.assertEqual(stages['reg-running'],'OAuth 授权 1')
        self.assertEqual(stages['refund-running'],'移出团队成员')
        self.assertTrue(all('current_stage' not in r for r in listing(self.user)['items']))
        self.assertEqual(next(r for r in admin_orders(page=1,page_size=10)['items'] if r['id']=='sub-running')['current_stage'],stages['sub-running'])
        reg=[{'id':'reg-running','status':'running'}];attach(reg,'registration')
        refund=[{'id':'refund-running','status':'processing'}];attach(refund,'refund')
        self.assertEqual(reg[0]['current_stage'],stages['reg-running'])
        self.assertEqual(refund[0]['current_stage'],stages['refund-running'])

    def test_actual_settlement_frozen_and_feature_projection(self):
        self.sub('paid');self.sub('holding',status='review');self.reg('reg',status='partial',delivered=1)
        for oid,kind,amount,ref in [('paid','freeze',0,'freeze-paid'),('paid','charge',-500,'charge-paid'),('paid','refund',100,'refund-paid'),('holding','freeze',0,'freeze-holding')]:
            db.execute("INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,description,order_id) VALUES('u',$1,$2,1000,300,$3,'fixture',$4)",kind,amount,ref,oid)
        db.execute("INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,description,business,registration_order_id,frozen_change) VALUES('u','charge',-500,1000,300,'registration-partial','fixture','registration','reg',-500),('u','freeze',0,1000,300,'registration-reserve','fixture','registration','reg',1500)")
        result=listing(self.user)
        self.assertEqual(result['summary']['settled'],900);self.assertEqual(result['summary']['frozen'],1500)
        byid={r['id']:r for r in result['items']};self.assertEqual(byid['paid']['settled'],400);self.assertEqual(byid['reg']['frozen'],1000)
        db.execute("INSERT INTO settings(key,value) VALUES('features','{\"wallet\":false,\"orders\":false}')")
        result=listing(self.user)
        self.assertNotIn('settled',result['summary']);self.assertFalse(result['show_fees']);self.assertFalse(result['can_view_orders'])
        for row in result['items']:
            self.assertIsNone(row['href']);self.assertNotIn('fee',row);self.assertNotIn('settled',row)

    def test_http_permissions_feature_off_and_input_validation(self):
        for key,value in DEFAULTS.items():db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        for owner in ('u','v','admin'):db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')",digest('activity-'+owner),owner)
        user=TestClient(app,cookies={'xupay_session':'activity-u'});admin=TestClient(app,cookies={'xupay_session':'activity-admin'})
        self.sub('own');self.sub('foreign','v')
        self.assertEqual(TestClient(app).get('/api/activity').status_code,401)
        self.assertEqual(user.get('/api/admin/activity').status_code,403)
        self.assertEqual(user.get('/api/activity').json()['total'],1)
        self.assertEqual(admin.get('/api/admin/activity').json()['total'],2)
        for query in ('page=0','page_size=0','page_size=999','state=broken','business=broken','start=invalid','start=2026-02-01&end=2026-01-01'):
            self.assertEqual(user.get('/api/activity?'+query).status_code,400,query)
        db.execute("UPDATE settings SET value=value||'{\"activity\":false}' WHERE key='features'")
        self.assertEqual(user.get('/api/activity').status_code,403)
        self.assertEqual(admin.get('/api/activity').status_code,403)
        self.assertEqual(admin.get('/api/admin/activity').status_code,200)

    def test_renewal_and_subscription_followup_statuses(self):
        self.sub('followup',activation='review')
        for owner,status in [('u','disabled'),('u','review'),('v','checking')]:
            ident=owner+'-'+status
            db.execute("INSERT INTO renewal_tasks(id,user_id,email,account_hash,credentials_enc,credentials_expires_at,status) VALUES($1,$2,'renew@test.example',$1,'synthetic',now()+interval '1 hour',$3)",ident,owner,status)
        result=listing(self.user)
        self.assertEqual(result['total'],3);self.assertEqual(result['summary']['completed'],1);self.assertEqual(result['summary']['attention'],2)
        renewal=next(row for row in result['items'] if row['id']=='u-disabled')
        self.assertEqual(renewal['workflow'],'renewal');self.assertEqual(renewal['settled'],0)
        self.assertTrue(renewal['href'].startswith('#/renewal'))
        self.assertEqual(listing(self.admin_user,admin=True)['summary']['customers'],2)

    def test_admin_renewal_record_permissions_and_safe_fields(self):
        for key,value in DEFAULTS.items():db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        for owner in ('u','admin'):db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')",digest('record-'+owner),owner)
        db.execute("INSERT INTO renewal_tasks(id,user_id,email,account_hash,credentials_enc,credentials_expires_at,status,message) VALUES('record','u','renew@test.example','private-hash','private-credentials',now()+interval '1 hour','disabled','已关闭自动续费')")
        db.execute("INSERT INTO renewal_events(task_id,status,message) VALUES('record','disabled','关闭结果已核实')")
        endpoint='/api/admin/renewals/record'
        self.assertEqual(TestClient(app).get(endpoint).status_code,401)
        self.assertEqual(TestClient(app,cookies={'xupay_session':'record-u'}).get(endpoint).status_code,403)
        admin=TestClient(app,cookies={'xupay_session':'record-admin'})
        response=admin.get(endpoint);self.assertEqual(response.status_code,200)
        result=response.json()
        self.assertEqual(set(result['task']),{'id','email','status','message','created_at','updated_at','owner'})
        self.assertEqual(result['events'][0]['message'],'关闭结果已核实')
        self.assertNotIn('private-',response.text)
        self.assertEqual(admin.get('/api/admin/renewals/missing').status_code,404)

    def test_large_history_concurrent_pages_and_snapshot_totals(self):
        db.execute("""INSERT INTO orders(id,user_id,product_id,product_name,email,status,fee,idempotency_key,request_hash,workflow,activation,renewal)
          SELECT 'load-'||n,CASE WHEN n%2=0 THEN 'u' ELSE 'v' END,'plus','Plus','load@test.example','succeeded',500,'load-'||n,'test','subscription','active','disabled' FROM generate_series(1,3000) n""")
        db.execute("""INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,order_id,description)
          SELECT user_id,'charge',-500,0,0,id,id,'fixture' FROM orders""")
        def read(n):
            start=time.monotonic();result=listing(self.user,page=1+n%30,page_size=50)
            self.assertEqual(result['total'],1500);self.assertEqual(result['summary']['settled'],750000)
            self.assertEqual(len(result['items']),50)
            return time.monotonic()-start
        with ThreadPoolExecutor(max_workers=50) as pool:elapsed=sorted(pool.map(read,range(100)))
        print('activity DB: 100 reads / 50 concurrency / 3000 orders + ledger; errors=0 p50=%.3fs p95=%.3fs max=%.3fs'%(elapsed[49],elapsed[94],elapsed[-1]))

