import unittest
from unittest.mock import AsyncMock,patch
from fastapi.testclient import TestClient
from starlette.requests import Request
import test_dashboard as fixture
from backend import db
from backend.common import digest
from backend.customer_finance import customers,customer_ledger
from backend.main import app
from backend.seed import DEFAULTS


class CustomerFinanceTests(unittest.TestCase):
    setUp=fixture.DashboardTests.setUp
    tearDown=fixture.DashboardTests.tearDown

    def report_request(self,query=''):
        return Request({'type':'http','method':'GET','path':'/api/admin/finance/customers','headers':[],'query_string':query.encode()})

    def entry(self,ref,kind,amount,business='subscription',owner='u',at='2026-11-11T01:00:00Z',balance=1000,frozen=0,**changes):
        db.execute('INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,business,description,created_at,available_change,frozen_change) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)',owner,kind,amount,balance,frozen,ref,business,'记录 '+ref,at,changes.get('available'),changes.get('held'))

    def test_shared_wallet_three_businesses_and_pagination(self):
        self.entry('credit','redeem',1000,'registration')
        self.entry('reg','charge',-200,'registration');self.entry('sub','charge',-300)
        self.entry('ret','charge',-400,'refund');self.entry('back','refund',100)
        self.entry('adjust','adjustment',50);self.entry('reward','invite_reward',20)
        self.entry('freeze','freeze',0,available=-500,held=500)
        self.entry('admin','redeem',999,owner='admin')
        request=self.report_request('start=2026-11-11&end=2026-11-11')
        result=customers(request);row=next(r for r in result['items'] if r['id']=='u')
        self.assertEqual((row['credited'],row['adjusted'],row['rewarded'],row['charged'],row['returned']),(1000,50,20,900,100))
        self.assertEqual([row[k] for k in ('registration','subscription','refund')],[200,200,400])
        self.assertEqual(result['total'],2)
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code) SELECT 'extra-'||n,'extra-'||n||'@test.example','extra','synthetic','extra-'||n FROM generate_series(1,31) n")
        self.assertEqual(len(customers(request,page_size=10)['items']),10)
        self.assertEqual(len(customers(request,page=4,page_size=10)['items']),3)
        self.assertEqual(customers(request,page=999)['page'],4)
        self.assertEqual(customers(request,q='extra-31')['total'],1)
        self.assertEqual(customers(request,q='%')['total'],0)
        self.assertNotIn('password_hash',str(result))

    def test_ledger_uses_prior_balance_outside_date_and_keeps_all_pages(self):
        self.entry('before','redeem',1000,at='2026-11-10T01:00:00Z',balance=1000,frozen=0)
        self.entry('freeze','freeze',0,balance=1000,frozen=300)
        self.entry('charge','charge',-300,balance=700,frozen=0)
        for n in range(35):self.entry('after-'+str(n),'adjustment',1,balance=701+n,frozen=0)
        request=self.report_request('start=2026-11-11&end=2026-11-11')
        before=db.one("SELECT balance,frozen FROM users WHERE id='u'")
        first=customer_ledger('u',request);second=customer_ledger('u',request,page=2)
        self.assertEqual((first['total'],len(first['items']),len(second['items'])),(37,25,12))
        rows={r['description']:r for r in first['items']+second['items']}
        self.assertEqual((rows['记录 freeze']['available_delta'],rows['记录 freeze']['frozen_delta']),(-300,300))
        self.assertEqual((rows['记录 charge']['available_delta'],rows['记录 charge']['frozen_delta']),(0,-300))
        self.assertEqual(rows['记录 after-0']['business'],'shared')
        self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),before)

    def test_http_permissions_validation_and_admin_mother_visibility(self):
        for key,value in DEFAULTS.items():db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        for owner in ('u','admin'):db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')",digest('finance-'+owner),owner)
        admin=TestClient(app,cookies={'xupay_session':'finance-admin'});customer=TestClient(app,cookies={'xupay_session':'finance-u'})
        for endpoint in ('/api/admin/finance/customers','/api/admin/finance/customers/u'):
            self.assertEqual(TestClient(app).get(endpoint).status_code,401)
            self.assertEqual(customer.get(endpoint).status_code,403)
            self.assertEqual(admin.get(endpoint).status_code,200)
        for query in ('page=0','page_size=501','start=invalid','start=2026-11-12&end=2026-11-11'):
            self.assertEqual(admin.get('/api/admin/finance/customers?'+query).status_code,400)
        self.assertEqual(admin.get('/api/admin/finance/customers/missing').status_code,404)
        db.execute("INSERT INTO refund_proxies(id,name,secret) VALUES('proxy','Proxy','private-proxy')")
        db.execute("INSERT INTO refund_mothers(id,name,email,team_id,secret,proxy_id) VALUES('mother','母号 2','mother@test.example','team','private-mother','proxy')")
        db.execute("INSERT INTO refunds(id,user_id,email,credentials,status,fee) VALUES('refund','u','child@test.example','private-child','succeeded',100)")
        db.execute("INSERT INTO refund_execution(refund_id,mother_id,step,released) VALUES('refund','mother','completed',true)")
        with patch('backend.refunds.call',new=AsyncMock(return_value={'order':{'id':'refund'},'events':[]})):
            response=admin.get('/api/admin/refund/orders/refund');self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['mother']['name'],'母号 2');self.assertNotIn('private-',response.text)
            self.assertEqual(customer.get('/api/admin/refund/orders/refund').status_code,403)
            self.assertNotIn('mother',customer.get('/api/refund/orders/refund').json())
            db.execute("UPDATE refund_mothers SET deleted_at=now() WHERE id='mother'")
            self.assertTrue(admin.get('/api/admin/refund/orders/refund').json()['mother']['deleted_at'])
