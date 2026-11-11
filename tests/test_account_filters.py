import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
import test_registration_account as fixture
from backend import db, admin_accounts
from backend.common import BusinessError, digest
from backend.seed import DEFAULTS


class AccountFilterTests(unittest.TestCase):
    tearDown=fixture.RegistrationAccountTests.tearDown

    def setUp(self):
        fixture.RegistrationAccountTests.setUp(self)
        for key,value in DEFAULTS.items():
            db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        self.tiers=[{'code':'base','name':'普通','threshold':0,'discount':100},{'code':'gold','name':'金卡','threshold':1000,'discount':90}]
        db.execute("UPDATE settings SET value=$1 WHERE key='tiers'",{'items':self.tiers})

    def test_search_all_accounts_with_combined_filters_and_stable_paging(self):
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code,created_at) SELECT 'fixture-'||lpad(n::text,4,'0'),'account'||n||'@example.test','分页客户','fixture','fixture-'||n,'2020-01-01' FROM generate_series(1,525) n")
        ids=[]
        for page in range(1,7):
            result=admin_accounts.users(q='分页客户',page=page,page_size=100)
            self.assertEqual(result['total'],525);ids.extend(r['id'] for r in result['items'])
        self.assertEqual(len(set(ids)),525);self.assertEqual(ids,sorted(ids,reverse=True))
        self.assertEqual(admin_accounts.users(q='fixture-0001')['items'][0]['email'],'account1@example.test')
        self.assertEqual(admin_accounts.users(q='ACCOUNT525@EXAMPLE.TEST')['total'],1)
        db.execute("UPDATE users SET role='admin',status='disabled',membership_code='gold' WHERE id='fixture-0001'")
        result=admin_accounts.users(q='分页客户',role='admin',status='disabled',tier='gold')
        self.assertEqual([r['id'] for r in result['items']],['fixture-0001'])
        db.execute("UPDATE users SET deleted_at=now() WHERE id='fixture-0001'")
        self.assertEqual(admin_accounts.users(q='fixture-0001')['total'],0)

    def test_effective_membership_and_current_page_prices(self):
        db.execute("INSERT INTO products(id,name,description,fee) VALUES('plus','Plus','fixture',1000)")
        db.execute("INSERT INTO orders(id,user_id,product_id,product_name,email,status,fee,idempotency_key,request_hash) VALUES('paid','u','plus','Plus','u@example.test','succeeded',1500,'paid','fixture')")
        db.execute("INSERT INTO ledger(user_id,order_id,kind,amount,balance_after,frozen_after,reference,description) VALUES('u','paid','charge',-1500,0,0,'paid','fixture')")
        db.execute("UPDATE users SET membership_code='gold' WHERE id='v'")
        db.execute("INSERT INTO user_service_prices(user_id,service,fee) VALUES('u','refund',0)")
        self.assertEqual({r['id'] for r in admin_accounts.users(tier='gold')['items']},{'u','v'})
        db.execute("UPDATE users SET membership_until=now()-interval '1 day' WHERE id='v'")
        result=admin_accounts.users(tier='gold')
        self.assertEqual([r['id'] for r in result['items']],['u']);self.assertEqual(result['items'][0]['service_prices'],{'refund':0})
        self.assertEqual(admin_accounts.users(tier='base')['items'][0]['id'],'v')
        db.execute("UPDATE users SET membership_code='base' WHERE id='u'")
        self.assertEqual(admin_accounts.users(tier='gold')['total'],0)
        self.assertEqual(admin_accounts.users(tier='base')['total'],2)

    def test_literal_search_empty_pages_and_validation(self):
        db.execute("UPDATE users SET name='百分%_客户' WHERE id='u'")
        self.assertEqual(admin_accounts.users(q='%_')['total'],1)
        self.assertEqual(admin_accounts.users(q="' OR 1=1 --")['total'],0)
        result=admin_accounts.users(q='missing',page=5)
        self.assertEqual((result['page'],result['total'],result['items'],result['has_more']),(1,0,[],False))
        for values in ({'q':'x'*201},{'role':'owner'},{'status':'pending'},{'tier':'missing'},{'page':0},{'page_size':501}):
            with self.assertRaises(BusinessError):admin_accounts.users(**values)

    def test_http_permissions_and_concurrent_read_only_filters(self):
        from backend.main import app
        db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,'u',now()+interval '1 hour')",digest('fixture-user'))
        with patch('backend.main.worker_loop'), TestClient(app,raise_server_exceptions=False) as client:
            self.assertEqual(client.get('/api/admin/users').status_code,401)
            client.cookies.set('xupay_session','fixture-user')
            self.assertEqual(client.get('/api/admin/users?q=u').status_code,403)
            self.assertEqual(client.post('/api/admin/users/batch-settings',json={'ids':['v'],'changes':{'fee_override':1}}).status_code,403)
            db.execute("UPDATE users SET role='admin' WHERE id='u'")
            response=client.get('/api/admin/users?page_size=1')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['total'],2)
        before=db.all('SELECT id,balance,frozen FROM users ORDER BY id')
        with ThreadPoolExecutor(max_workers=20) as pool:
            results=list(pool.map(lambda i:admin_accounts.users(q=('u@' if i%2 else 'v@')),range(50)))
        self.assertTrue(all(r['total']==1 for r in results))
        self.assertEqual(before,db.all('SELECT id,balance,frozen FROM users ORDER BY id'))

    def test_bulk_partial_prices_membership_and_atomic_rejection(self):
        self.request.state.user={'id':'u','role':'admin'}
        db.execute("UPDATE settings SET value=value||$1::jsonb WHERE key='payment'",{'subscription_flow':'cslive_go_direct_pay'})
        db.execute("INSERT INTO products(id,name,description,fee) VALUES('plus','Plus','fixture',500)")
        db.execute("INSERT INTO user_service_prices(user_id,service,fee) VALUES('u','registration_oauth',999),('v','registration_oauth',888)")
        db.execute("INSERT INTO user_product_prices(user_id,product_id,fee) VALUES('u','plus',500),('v','plus',600)")
        before=db.all('SELECT id,balance,frozen FROM users ORDER BY id')
        result=admin_accounts.batch_settings_value({'ids':['u','v'],'changes':{'service_prices':{'registration_standard':123,'refund':0},'product_prices':{'plus':None},'membership':{'code':'gold','until':None}}},self.request)
        self.assertEqual(result['updated'],2)
        self.assertEqual(db.scalar('SELECT count(*) FROM user_product_prices'),0)
        self.assertEqual(db.scalar("SELECT fee FROM user_service_prices WHERE user_id='u' AND service='registration_oauth'"),999)
        self.assertEqual(db.scalar("SELECT fee FROM user_service_prices WHERE user_id='v' AND service='registration_oauth'"),888)
        self.assertEqual(admin_accounts.users(tier='gold')['total'],2)
        admin_accounts.batch_settings_value({'ids':['v'],'changes':{'service_prices':{'refund':None},'membership':{'code':'','until':None}}},self.request)
        self.assertEqual(admin_accounts.users(tier='base')['items'][0]['id'],'v')
        for changes in ({'fee_override':5,'service_prices':{'unknown':1}},{'membership':{'code':'missing','until':None}},{'membership':{'code':'gold','until':'2020-01-01T00:00:00Z'}},{'service_prices':{}},{'fee_override':True}):
            with self.assertRaises(BusinessError):admin_accounts.batch_settings_value({'ids':['u','v'],'changes':changes},self.request)
        self.assertIsNone(db.scalar("SELECT fee_override FROM users WHERE id='u'"))
        with self.assertRaises(BusinessError):admin_accounts.batch_settings_value({'ids':['u','missing'],'changes':{'fee_override':1}},self.request)
        self.assertEqual(before,db.all('SELECT id,balance,frozen FROM users ORDER BY id'))

    def test_bulk_five_hundred_accounts_and_page_size(self):
        self.request.state.user={'id':'u','role':'admin'}
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code) SELECT 'batch-'||n,'batch'||n||'@example.test','批量客户','fixture','batch-'||n FROM generate_series(1,500) n")
        rows=admin_accounts.users(q='批量客户',page_size=500)
        self.assertEqual(len(rows['items']),500);self.assertFalse(rows['has_more'])
        ids=[row['id'] for row in rows['items']]
        result=admin_accounts.batch_settings_value({'ids':ids,'changes':{'fee_override':0,'membership':{'code':'gold','until':None}}},self.request)
        self.assertEqual(result['updated'],500)
        self.assertEqual(admin_accounts.users(q='批量客户',tier='gold',page_size=10)['total'],500)
        self.assertEqual(db.scalar("SELECT count(*) FROM users WHERE id LIKE 'batch-%' AND fee_override=0"),500)
