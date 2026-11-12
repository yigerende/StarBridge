"""Real PostgreSQL paging, global ordering and authorization regression."""
import unittest, time, json, subprocess, os, threading
from unittest.mock import patch
from psycopg2.pool import ThreadedConnectionPool
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
import test_registration_account as fixture
from backend import db
from backend.common import digest
from backend.seed import DEFAULTS
from backend.main import app

class ListPaginationTests(unittest.TestCase):
    tearDown=fixture.RegistrationAccountTests.tearDown
    def setUp(self):
        fixture.RegistrationAccountTests.setUp(self)
        db.close();db._pool=ThreadedConnectionPool(12,12,os.environ['DATABASE_URL'])
        slots=patch.object(db,'_slots',threading.BoundedSemaphore(12));slots.start();self.addCleanup(slots.stop)
        for key,value in DEFAULTS.items():db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        db.execute("UPDATE settings SET value=value||'{\"api_per_minute\":10000}'::jsonb WHERE key='risk'")
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code,role) VALUES('admin','admin@example.test','Admin','synthetic','admin','admin')")
        for uid in ('u','v','admin'):db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')",digest('pagination-'+uid),uid)
        self.user=TestClient(app,cookies={'xupay_session':'pagination-u'})
        self.admin_client=TestClient(app,cookies={'xupay_session':'pagination-admin'})
        self.other=TestClient(app,cookies={'xupay_session':'pagination-v'})
        db.execute("INSERT INTO products(id,name,description,fee) VALUES('plus','Plus','fixture',100)")
        db.execute("INSERT INTO orders(id,user_id,product_id,product_name,email,status,fee,idempotency_key,request_hash,workflow,created_at) SELECT 'o'||lpad(n::text,4,'0'),'u','plus','Plus','customer'||lpad(n::text,4,'0')||'@test.example','succeeded',n,'key'||n,'synthetic','subscription','2025-01-01' FROM generate_series(1,525)n")
        db.execute("INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,description,created_at) SELECT 'u','adjustment',n,n,0,'entry'||n,'record'||n,'2025-01-01' FROM generate_series(1,525)n")
        db.execute("INSERT INTO topups(id,user_id,amount,status,created_at) SELECT 't'||lpad(n::text,4,'0'),'u',n,'approved','2025-01-01' FROM generate_series(1,525)n")
        db.execute("INSERT INTO api_keys(id,user_id,name,hash,prefix,created_at) SELECT 'k'||lpad(n::text,4,'0'),'u','Key'||n,'hash'||n,'XP-'||n,'2025-01-01' FROM generate_series(1,525)n")
        db.execute("INSERT INTO renewal_tasks(id,user_id,email,account_hash,status,credentials_enc,credentials_expires_at,created_at) SELECT 'r'||lpad(n::text,4,'0'),'u','renew'||n||'@example.test','a'||n,'disabled','PRIVATE',now(),'2025-01-01' FROM generate_series(1,525)n")
        db.execute("INSERT INTO audit_events(actor_id,action,target,detail,ip,created_at) SELECT 'u','test','target'||n,'detail','192.0.2.1','2025-01-01' FROM generate_series(1,525)n")
        db.execute("INSERT INTO risk_events(user_id,category,severity,ip,detail,created_at) SELECT 'u','login','low','192.0.2.1','event'||n,'2025-01-01' FROM generate_series(1,525)n")
        db.execute("INSERT INTO announcements(id,title,body) SELECT 'a'||lpad(n::text,4,'0'),'Notice'||n,'body' FROM generate_series(1,525)n")
        db.execute("INSERT INTO mail_deliveries(id,recipient,purpose,status) SELECT 'mail'||lpad(n::text,4,'0'),'user'||n||'@example.test','test','sent' FROM generate_series(1,525)n")
    def get(self,client,path,**params):
        r=client.get(path,params=params);self.assertEqual(r.status_code,200,(path,params,r.text[:500]));return r.json()
    def test_all_real_pages_filters_and_owner_scope(self):
        paths=['/api/orders','/api/wallet/ledger','/api/wallet/recharges','/api/keys','/api/renewals']
        for path in paths:
            for size in (10,50,100,500):
                with self.subTest(path=path,size=size):
                    data=self.get(self.user,path,page_size=size,sort_by='created_at',sort_dir='asc');self.assertEqual(data['total'],525);self.assertEqual(len(data['items']),size)
            first=self.get(self.user,path,page_size=500,sort_by='created_at',sort_dir='asc')
            last=self.get(self.user,path,page=2,page_size=500,sort_by='created_at',sort_dir='asc')
            self.assertEqual(len(last['items']),25);self.assertEqual(len({str(x['id']) for x in first['items']+last['items']}),525)
            self.assertEqual(self.get(self.other,path,page_size=500)['total'],0)
            self.assertEqual(self.user.get(path,params={'sort_by':'id;DROP TABLE users'}).status_code,400)
        for path in ('/api/admin/orders','/api/admin/topups','/api/admin/audit','/api/admin/announcements','/api/admin/mail/logs'):
            with self.subTest(path=path):
                d=self.get(self.admin_client,path,page_size=500);self.assertEqual(d['total'],525);self.assertEqual(len(d['items']),500)
                self.assertEqual(self.user.get(path).status_code,403)
        self.assertEqual(self.get(self.admin_client,'/api/admin/risk',all_time='true',q='event',page_size=500)['total'],525)
        self.assertEqual(self.get(self.user,'/api/orders',q='customer0001')['total'],1)
        d=self.get(self.user,'/api/orders',sort_by='fee',sort_dir='desc',page_size=10)
        self.assertEqual([x['fee'] for x in d['items']],list(range(525,515,-1)))
        d=self.get(self.user,'/api/wallet/ledger',sort_by='amount',sort_dir='asc',page_size=10)
        self.assertEqual([x['amount'] for x in d['items']],list(range(1,11)))
        self.assertEqual(d['summary']['adjusted'],525*526//2)
        self.assertNotIn('PRIVATE',json.dumps(self.get(self.user,'/api/renewals',page_size=500)))
    def test_whitelisted_columns_and_finance_snapshot(self):
        checks={
          '/api/admin/users':['account','balance','used_balance','membership','orders'],
          '/api/keys':['name','prefix','revoked','last_used'],
          '/api/admin/topups':['email','amount','status','created_at'],
          '/api/admin/audit':['actor','action','target','ip'],
          '/api/admin/risk':['severity','category','email','ip','last_seen','occurrences','resolved'],
          '/api/admin/finance/customers':['email','credited','adjusted','registration','subscription','refund','settled','available'],
          '/api/admin/finance':['email','name','spent','orders','balance','frozen','fee_override'],
          '/api/wallet/recharges':['source','status','amount','available_delta','frozen_delta','balance_after'],
        }
        before=db.one("SELECT balance,frozen FROM users WHERE id='u'")
        for path,fields in checks.items():
            for field in fields:
                for direction in ('asc','desc'):
                    with self.subTest(path=path,field=field,direction=direction):self.get(self.admin_client if '/admin/' in path else self.user,path,page_size=10,sort_by=field,sort_dir=direction,start='2025-01-01',end='2026-11-11')
        self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),before)
    def test_concurrent_pages_have_no_cross_user_data(self):
        def query(n):
            client=self.other if n%2 else self.user
            started=time.perf_counter();data=self.get(client,'/api/orders',page=1,page_size=500,sort_by='fee',sort_dir='asc')
            self.assertEqual(data['total'],0 if n%2 else 525)
            return time.perf_counter()-started
        with ThreadPoolExecutor(max_workers=30) as pool:elapsed=sorted(pool.map(query,range(60)))
        print(f' pagination: 60 reads / 30 concurrency / 525 orders / 2 scopes; errors=0 p50={elapsed[30]:.3f}s p95={elapsed[56]:.3f}s max={elapsed[-1]:.3f}s')

    def test_registration_real_database_and_authorization(self):
        r=subprocess.run(['node','tests/list_pagination.cjs'],cwd=fixture.ROOT,env={**os.environ,'XUFU_INTEGRATION':'1','REGISTRATION_GATEWAY_SECRET':'synthetic-pagination-gateway-secret-32'},capture_output=True,text=True,encoding='utf-8',timeout=90)
        self.assertEqual(r.returncode,0,r.stdout+'\n'+r.stderr)
        print(r.stdout.strip())

    def test_service_quotes_page_all_prices_filters_and_owner_pricing(self):
        db.execute("INSERT INTO products(id,name,description,fee,enabled) SELECT 'plan'||lpad(n::text,4,'0'),'Plan '||lpad(n::text,4,'0'),'fixture',n,n%2=1 FROM generate_series(1,525)n")
        path='/api/membership/services'
        for size in (10,50,100,500):
            d=self.get(self.user,path,business='subscription',q='Plan ',page_size=size,sort_by='fee',sort_dir='desc')
            self.assertEqual(d['total'],525);self.assertEqual(len(d['items']),size)
            self.assertEqual([r['fee'] for r in d['items']],list(range(525,525-size,-1)))
        last=self.get(self.user,path,business='subscription',q='Plan ',page=2,page_size=500,sort_by='fee',sort_dir='desc')
        self.assertEqual(len(last['items']),25);self.assertEqual(last['items'][-1]['fee'],1)
        self.assertEqual(self.get(self.user,path,q='Plan ',status='disabled')['total'],262)
        db.execute("UPDATE users SET fee_override=7 WHERE id='u'")
        mine=self.get(self.user,path,q='Plan 0525')['items'][0]
        other=self.get(self.other,path,q='Plan 0525')['items'][0]
        self.assertEqual(mine['member_fee'],7);self.assertEqual(mine['price_source'],'account_override')
        self.assertEqual(other['member_fee'],525)
        for field in ('business','name','fee','member_fee','price_source','enabled'):
            for direction in ('asc','desc'):self.get(self.user,path,sort_by=field,sort_dir=direction)
        self.assertEqual(self.user.get(path,params={'sort_by':'invalid'}).status_code,400)
        self.assertEqual(TestClient(app).get(path).status_code,401)
        compact=self.get(self.user,'/api/membership',include_quotes='false')
        self.assertEqual(compact['services'],[]);self.assertEqual(compact['products'],[])
        db.execute("UPDATE settings SET value=value||'{\"membership\":false}'::jsonb WHERE key='features'")
        self.assertEqual(self.user.get(path).status_code,403)

    def test_membership_announcements_and_paged_configuration_preserve_other_rows(self):
        from backend.common import config
        from backend.country_pool import COUNTRY_REFERENCE
        db.execute("INSERT INTO ledger(user_id,order_id,kind,amount,balance_after,frozen_after,reference,description) SELECT 'u',id,'charge',-fee,0,0,'growth-'||id,'growth' FROM orders")
        for size in (10,50,100,500):
            data=self.get(self.user,'/api/membership',page_size=size,sort_by='spent',sort_dir='desc')
            self.assertEqual(data['total'],525);self.assertEqual(len(data['growth']),size);self.assertEqual(data['growth'],data['items']);self.assertEqual(data['growth'][0]['spent'],525)
        self.assertEqual(self.get(self.other,'/api/membership')['total'],0)
        summary=self.get(self.admin_client,'/api/admin/membership',summary_only='true')
        self.assertNotIn('users',summary)
        self.assertEqual(self.user.get('/api/admin/membership').status_code,403)
        db.execute('UPDATE announcements SET published=true')
        d=self.user.post('/api/announcements/query',json={'page_size':500,'sort_by':'title','sort_dir':'asc'}).json()
        self.assertEqual(d['total'],525);self.assertEqual(len(d['items']),500);self.assertEqual(set(d['index'][0]),{'id','version'})
        seen={r['id']:r['version'] for r in d['index']}
        self.assertEqual(self.user.post('/api/announcements/query',json={'seen':seen,'unread':True}).json()['total'],0)
        db.execute("UPDATE announcements SET body='edited after read' WHERE id='a0001'")
        changed=self.user.post('/api/announcements/query',json={'seen':seen,'unread':True}).json()
        self.assertEqual(changed['total'],1);self.assertEqual(changed['items'][0]['id'],'a0001')
        self.assertEqual(self.user.post('/api/announcements/query',json={'seen':['wrong']}).status_code,400)
        countries=list(COUNTRY_REFERENCE.values())[:211]
        db.execute("INSERT INTO settings(key,value) VALUES('country_pool',$1) ON CONFLICT(key) DO UPDATE SET value=settings.value||excluded.value",{'countries':countries})
        regions=[{**r,'enabled':False,'sort':i} for i,r in enumerate(countries)]
        db.execute("INSERT INTO settings(key,value) VALUES('regions',$1) ON CONFLICT(key) DO UPDATE SET value=excluded.value",{'items':regions})
        profiles=[{'id':'bp_'+str(i),'name':f'Profile {i:03}','first_name':'Test','last_name':'Account','state':'CA','city':'Los Angeles','line1':f'{i+1} Test Street','postal_code':'90001','enabled':True,'test_only':True} for i in range(61)]
        db.execute("UPDATE settings SET value=value||$1 WHERE key='billing'",{'address_templates':profiles})
        for endpoint,total in [('/api/admin/regions',211),('/api/admin/country-pool/countries',211),('/api/admin/billing-profiles',61)]:
            for size in (10,50,100,500):
                d=self.get(self.admin_client,endpoint,page_size=size);self.assertEqual(d['total'],total);self.assertEqual(len(d['items']),min(size,total))
            self.assertEqual(self.user.get(endpoint).status_code,403)
        original=config('regions')['items'];row=original[-1];row={**row,'name':'Updated region'}
        r=self.admin_client.put('/api/admin/regions/'+row['code'],json=row);self.assertEqual(r.status_code,200,r.text)
        updated=config('regions')['items'];self.assertEqual(len(updated),211);self.assertEqual(updated[:-1],original[:-1]);self.assertEqual(updated[-1]['name'],'Updated region')
        r=self.admin_client.put('/api/admin/billing-profiles/bp_60',json={**profiles[-1],'name':'Updated profile'});self.assertEqual(r.status_code,200,r.text)
        after=config('billing')['address_templates'];self.assertEqual(len(after),61);self.assertEqual(after[:-1],profiles[:-1]);self.assertEqual(after[-1]['name'],'Updated profile')

if __name__=='__main__':unittest.main()
