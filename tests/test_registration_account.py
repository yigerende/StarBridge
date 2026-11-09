import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import unittest
from unittest.mock import patch
import psycopg2
from psycopg2 import sql
from starlette.requests import Request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend import db, registration_account as account
from backend.common import digest, BusinessError


class RegistrationAccountTests(unittest.TestCase):
    def setUp(self):
        self.name='registration_account_'+secrets.token_hex(6)
        self.admin=psycopg2.connect('postgresql://gptreg_test@127.0.0.1:18983/postgres',connect_timeout=5)
        self.admin.autocommit=True
        with self.admin.cursor() as q:q.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.name)))
        self.env=patch.dict(os.environ,{'DATABASE_URL':'postgresql://gptreg_test@127.0.0.1:18983/'+self.name,'MASTER_KEY':base64.b64encode(secrets.token_bytes(32)).decode()})
        self.env.start();db.initialize()
        subprocess.run(['node','-e',"const {Database}=require('./src/platform/db');const d=new Database(process.env.DATABASE_URL);d.initialize().then(()=>d.close()).catch(e=>{console.error(e);process.exit(1)})"],cwd=ROOT/'services/gpt-register',env={**os.environ,'XUFU_INTEGRATION':'1'},capture_output=True,check=True,timeout=30)
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code,balance,frozen) VALUES('u','u@example.test','User','synthetic','invite',1000,300),('v','v@example.test','Other','synthetic','other',0,0)")
        db.execute("INSERT INTO platform_users(id,email,name,password_hash,xufu_user_id) VALUES('p','p@example.test','User','unused','u')")
        self.request=Request({'type':'http','method':'GET','path':'/api/keys','headers':[],'query_string':b'','client':('127.0.0.1',1234)})
        self.request.state.user={'id':'u','role':'member'}

    def tearDown(self):
        db.close();self.env.stop()
        with self.admin.cursor() as q:
            q.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s',(self.name,))
            q.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(self.name)))
        self.admin.close()

    def test_unified_redeem_is_atomic_under_sixty_concurrent_requests(self):
        from backend.redeem_codes import redeem
        code='XP-'+secrets.token_hex(20).upper()
        db.execute("INSERT INTO redeem_codes(id,code_hash,code_enc,prefix,amount,batch,expires_at) VALUES('code',$1,'','XP-test',700,'parallel',now()+interval '1 day')",digest(code))
        def attempt(_):
            try:return redeem(self.request,{'code':code})
            except BusinessError as e:return {'status':e.status}
        with ThreadPoolExecutor(max_workers=60) as pool:results=list(pool.map(attempt,range(60)))
        self.assertEqual(sum(r.get('ok') is True for r in results),1)
        self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),{'balance':1700,'frozen':300})
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),1)
        self.assertEqual(db.scalar('SELECT count(*) FROM platform_redeem_codes'),0)

    def test_pool_queues_sixty_four_reads_without_exhaustion(self):
        def read(_):return db.scalar('SELECT 7 FROM pg_sleep(0.025)')
        with ThreadPoolExecutor(max_workers=64) as pool:results=list(pool.map(read,range(128)))
        self.assertEqual(results,[7]*128)

    def test_risk_and_audit_share_one_console_and_resolve_both_records(self):
        from backend.admin_content import resolve
        db.execute("INSERT INTO platform_risk_events(dedupe,kind,category,severity,message,user_id,source_ip) VALUES('risk','abuse','access','critical','test event','p','127.0.0.2')")
        event=db.one('SELECT * FROM risk_events')
        self.assertEqual((event['user_id'],event['severity'],event['resolved']),('u','high',False))
        resolve(event['id'],self.request)
        self.assertIsNotNone(db.scalar('SELECT resolved_at FROM platform_risk_events'))
        db.execute('UPDATE platform_risk_events SET occurrences=3')
        self.assertEqual(db.scalar('SELECT occurrences FROM risk_events'),3)
        db.execute("INSERT INTO platform_audit(actor_id,action,target,detail) VALUES('p','order.cancel','order','cancelled')")
        item=db.one("SELECT * FROM audit_events WHERE action='registration.order.cancel'")
        self.assertEqual(item['actor_id'],'u')

    def test_subscription_finance_excludes_registration_credit(self):
        from backend.admin_content import finance_data
        db.execute("INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,business,description) VALUES('u','redeem',700,1700,300,'registration:code','registration','test'),('u','redeem',200,1900,300,'subscription:code','subscription','test')")
        report=finance_data(self.request)
        self.assertEqual(report['totals']['credited'],200)
        self.assertEqual(next(r for r in report['users'] if r['id']=='u')['credited'],200)

    def test_finance_overview_counts_shared_wallet_once_and_both_businesses(self):
        from backend.admin_content import finance_overview
        db.execute("INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,business,description) VALUES('u','redeem',1000,1000,0,'code','subscription','credit'),('u','charge',-200,800,0,'sub','subscription','charge'),('u','charge',-300,500,0,'reg','registration','charge')")
        report=finance_overview(self.request)
        self.assertEqual(report['totals'],dict(credited=1000,spent=500,adjusted=0))
        self.assertEqual(report['current'],dict(available=700,frozen=300))
        from backend.admin_content import export_finance
        import csv,io
        rows=list(csv.DictReader(io.StringIO(export_finance(self.request).body.decode('utf-8-sig'))))
        self.assertEqual(next(r for r in rows if r['账户']=='u@example.test')['当前可用分'],'700')
        db.execute("UPDATE users SET role='admin' WHERE id='u'")
        self.assertEqual(finance_overview(self.request)['totals']['credited'],0)
        including=Request({**self.request.scope,'query_string':b'include_admin=true'})
        self.assertEqual(finance_overview(including)['totals']['spent'],500)
        self.request.scope['query_string']=b'period=invalid'
        # Request caches query parameters; use a new request for independent filters.
        invalid=Request({**self.request.scope,'query_string':b'period=invalid'})
        with self.assertRaises(BusinessError):finance_overview(invalid)

    def test_account_adjustment_cannot_spend_reserved_funds(self):
        from backend.wallet import credit
        with self.assertRaisesRegex(BusinessError,'冻结积分'):
            with db.transaction() as q:credit(q,self.request,'u','adjustment','test-adjust','test',-800)
        self.assertEqual(db.scalar("SELECT balance FROM users WHERE id='u'"),1000)
        with db.transaction() as q:credit(q,self.request,'u','adjustment','test-adjust','test',-700)
        self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),{'balance':300,'frozen':300})

    def test_shared_wallet_credits_do_not_appear_as_subscription_or_registration_spend(self):
        from backend.customer_api import ledger
        kinds=['adjustment','topup','redeem','invite_reward','invite_reversal']
        for i,kind in enumerate(kinds):
            db.execute("INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,business,description) VALUES('u',$1,100,1000,300,$2,$3,'test')",kind,'credit-'+str(i),'registration' if i%2 else 'subscription')
        for business in ['subscription','registration']:
            db.execute("INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,business,description) VALUES('u','charge',-100,900,300,$1,$2,'test')",business+'-charge',business)
        before=db.all("SELECT id,business,amount,balance_after,frozen_after FROM ledger ORDER BY id")
        result=ledger(self.request,page=1,page_size=50)
        self.assertEqual({r['kind'] for r in result['items'] if r['business']=='shared'},set(kinds))
        for business in ['subscription','registration']:
            rows=[r for r in result['items'] if r['business']==business]
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['kind'],'charge')
        self.assertEqual(db.all("SELECT id,business,amount,balance_after,frozen_after FROM ledger ORDER BY id"),before)

    def test_openapi_references_and_security_resolve_in_single_document(self):
        from backend.registration_docs import document
        spec=document();ids=[]
        def walk(value):
            if isinstance(value,list):
                for v in value:walk(v)
            elif isinstance(value,dict):
                if '$ref' in value:
                    target=spec
                    for part in value['$ref'].removeprefix('#/').split('/'):target=target[part]
                if 'security' in value:
                    for entry in value['security']:
                        for scheme in entry:self.assertIn(scheme,spec['components']['securitySchemes'])
                for v in value.values():walk(v)
        walk(spec)
        ids=[op['operationId'] for methods in spec['paths'].values() for op in methods.values()]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertIn('/registration/open/v1/registration-orders',spec['paths'])
        error=spec['paths']['/registration/open/v1/registration-orders']['post']['responses']['400']
        self.assertTrue(error['description'])
        self.assertIn('content',error)

    def test_partner_rewards_keep_order_inviter_at_settlement(self):
        from backend.invitations import listing
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code) VALUES('old','old@example.test','Old inviter','synthetic','old')")
        db.execute("UPDATE users SET invited_by='v' WHERE id='u'")
        db.execute("INSERT INTO platform_users(id,email,name,password_hash,xufu_user_id) VALUES('old-p','old-p@example.test','Old','unused','old')")
        db.execute("INSERT INTO platform_orders(id,user_id,quantity,need_oauth,unit_price,source,request_key,request_hash,template,attempt_limit,timeout_minutes) VALUES('o','p',1,false,5,'web','test','hash','{}',1,20)")
        db.execute("INSERT INTO platform_partner_rewards(order_id,invitee_id,inviter_id,rate_percent,fee,amount) VALUES('o','p','old-p',20,5,1)")
        self.assertEqual(listing(self.request,'old')['stats']['earned'],100)
        self.assertEqual(listing(self.request,'v')['stats']['earned'],0)

if __name__=='__main__':unittest.main()
