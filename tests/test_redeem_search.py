"""Full-code searches against disposable PostgreSQL, using real admin routes."""
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import db, redeem_codes
from backend.common import digest, encrypt
from backend.main import app
from backend.seed import DEFAULTS
import test_registration_account as fixture


class RedeemSearchTests(unittest.TestCase):
    setUp_fixture = fixture.RegistrationAccountTests.setUp
    tearDown = fixture.RegistrationAccountTests.tearDown

    def setUp(self):
        self.setUp_fixture()
        for key,value in DEFAULTS.items():
            db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        db.execute("UPDATE settings SET value=value||$1::jsonb WHERE key='risk'",{'edge_per_minute':100000,'api_per_minute':10000})
        db.execute("INSERT INTO users(id,email,name,password_hash,role,invite_code) VALUES('admin','admin@search.test','Admin','fixture','admin','search-admin')")
        for owner in ('admin','u'):
            db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')",digest('search-'+owner),owner)
        self.client=TestClient(app)
        self.client.cookies.set('xupay_session','search-admin')
        self.addCleanup(self.client.close)

    def issue(self, id='target', status='active', used_at=None, expired=False):
        code='XP-ABCD'+digest(id)[:16].upper()+'DCBA'
        db.execute("INSERT INTO redeem_codes(id,code_hash,code_enc,prefix,amount,batch,status,expires_at,used_by,used_at) "
                   "VALUES($1,$2,$3,'XP-ABCD…DCBA',1200,$4,$5,now()+$6*interval '1 day',$7,$8)",
                   id,digest(code),encrypt(code),'batch-'+id,status,-1 if expired else 1,'u' if status=='used' else None,used_at)
        return code

    def get(self,path,**params):
        response=self.client.get(path,params=params)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def assert_private(self,result,code):
        self.assertNotIn(code,json.dumps(result))
        for row in result['items']:
            self.assertFalse({'code','code_enc','code_hash'} & set(row))

    def test_full_code_exact_match_normalization_and_status(self):
        # All fixtures deliberately share the visible prefix and suffix.
        codes={state:self.issue(state,'used' if state=='used' else 'disabled' if state=='disabled' else 'active',
                                 '2026-11-25T04:00:00Z' if state=='used' else None,state=='expired')
               for state in ('active','used','disabled','expired')}
        before=db.all('SELECT * FROM redeem_codes ORDER BY id')
        with patch.object(redeem_codes,'decrypt',side_effect=AssertionError('Search must not decrypt')):
            for state,code in codes.items():
                for search in (code,code.lower(),' \n'+code.lower()+'\t '):
                    with self.subTest(state=state,search=search):
                        result=self.get('/api/admin/codes',q=search)
                        self.assertEqual(result['total'],1)
                        self.assertEqual([(r['id'],r['status']) for r in result['items']],[(state,state)])
                        self.assert_private(result,code)
                self.assertEqual(self.get('/api/admin/codes',q=code,status=state)['total'],1)
                other='used' if state!='used' else 'active'
                self.assertEqual(self.get('/api/admin/codes',q=code,status=other)['total'],0)
        self.assertEqual(self.get('/api/admin/codes',q='XP-ABCD0000000000000000DCBA')['total'],0)
        self.assertEqual(self.get('/api/admin/codes',q='XP-ABCD…DCBA')['total'],4)
        self.assertEqual(db.all('SELECT * FROM redeem_codes ORDER BY id'),before)
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)

    def test_full_code_usage_date_summary_and_account_filters(self):
        code=self.issue('used','used','2026-11-25T04:00:00Z')
        self.issue('other-used','used','2026-11-25T05:00:00Z')
        self.issue('unused')
        path='/api/admin/codes/usage'
        with patch.object(redeem_codes,'decrypt',side_effect=AssertionError('Search must not decrypt')):
            for search in (code,'  '+code.lower()+'  '):
                result=self.get(path,q=search,period='day',date='2026-11-25')
                self.assertEqual(result['total'],1)
                self.assertEqual([r['id'] for r in result['items']],['used'])
                self.assertEqual(result['summary'],{'redemptions':1,'codes':1,'accounts':1,'amount':1200})
                self.assert_private(result,code)
            self.assertEqual(self.get(path,q=code,date='2026-11-24')['total'],0)
            self.assertEqual(self.get(path,q='u@example.test',date='2026-11-25')['total'],2)
            self.assertEqual(self.get(path,q='User',date='2026-11-25')['total'],2)

    def test_pagination_sort_and_literal_queries_search_all_pages(self):
        code=self.issue()
        db.execute("UPDATE redeem_codes SET created_at=now()-interval '1 day',batch='literal 100%_\\batch' WHERE id='target'")
        db.execute("INSERT INTO redeem_codes(id,code_hash,code_enc,prefix,amount,batch,expires_at) "
                   "SELECT 'other-'||n,'fixture-hash-'||n,'fixture','XP-other-'||n,n,'pagination',now()+interval '1 day' FROM generate_series(1,1001)n")
        for size in (10,50,100,500):
            found=self.get('/api/admin/codes',q=code,page_size=size,sort_by='amount',sort_dir='asc')
            self.assertEqual(found['total'],1)
            self.assertEqual(found['items'][0]['id'],'target')
            first=self.get('/api/admin/codes',q='pagination',page_size=size,sort_by='amount',sort_dir='asc')
            second=self.get('/api/admin/codes',q='pagination',page_size=size,page=2,sort_by='amount',sort_dir='asc')
            self.assertEqual(first['total'],1001)
            self.assertEqual(len(first['items']),size)
            self.assertEqual(first['items'][0]['amount'],1)
            self.assertEqual(second['items'][0]['amount'],size+1)
        for query in ('100%_',r'\batch','batch-target'):
            self.assertEqual(self.get('/api/admin/codes',q=query)['total'],0 if query=='batch-target' else 1)
        self.assertEqual(self.get('/api/admin/codes',q="' OR 1=1 --")['total'],0)

    def test_permissions_and_concurrent_read_only_search(self):
        code=self.issue('used','used','2026-11-25T04:00:00Z')
        for role,expected in (('u',403),(None,401)):
            client=TestClient(app)
            try:
                if role:client.cookies.set('xupay_session','search-'+role)
                for path in ('/api/admin/codes','/api/admin/codes/usage'):
                    self.assertEqual(client.get(path,params={'q':code}).status_code,expected)
            finally:
                client.close()
        before=db.all('SELECT * FROM redeem_codes ORDER BY id')
        def search(_):
            return self.client.get('/api/admin/codes',params={'q':code,'page_size':10})
        started=time.monotonic()
        with ThreadPoolExecutor(max_workers=20) as pool:
            results=list(pool.map(search,range(100)))
        self.assertTrue(all(r.status_code==200 and r.json()['total']==1 for r in results))
        self.assertEqual(db.all('SELECT * FROM redeem_codes ORDER BY id'),before)
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)
        print(f'Full-code search: 100 requests / 20 concurrent, errors=0, elapsed={time.monotonic()-started:.3f}s')


if __name__=='__main__':
    unittest.main()
