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

    def test_legacy_redeem_concurrent_once_and_projection(self):
        from backend.wallet import LEDGER_QUERY
        code='GR-'+secrets.token_hex(20)
        db.execute("INSERT INTO platform_redeem_codes(id,hash,prefix,points) VALUES('code',$1,'GR-test',7)",digest(code))
        def redeem():
            try:return account.redeem(self.request,code)
            except BusinessError as e:return {'status':e.status}
        with ThreadPoolExecutor(max_workers=6) as executor:results=list(executor.map(lambda _:redeem(),range(6)))
        self.assertEqual(sum(r.get('ok') is True for r in results),1)
        self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),{'balance':1700,'frozen':300})
        rows=db.all(LEDGER_QUERY,'u');self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['business'],rows[0]['available_delta'],rows[0]['frozen_delta']),('registration',700,0))

    def test_summary_and_legacy_key_revocation_are_scoped(self):
        db.execute("INSERT INTO platform_api_keys(id,user_id,name,hash,prefix,scopes) VALUES('k','p','legacy','synthetic','gr_test','[]')")
        self.assertEqual(account.legacy_keys('u')[0]['id'],'registration:k')
        self.assertEqual(account.legacy_keys('v'),[])
        self.assertEqual(account.revoke_key('v','k'),0)
        self.assertEqual(account.revoke_key('u','k'),1)
        self.assertEqual(account.summary('u')['orders'],0)
        self.assertEqual(db.scalar('SELECT count(*) FROM '+account.reward_source()+' r'),0)

    def test_fractional_registration_ledger_keeps_cent_precision(self):
        db.execute("INSERT INTO platform_ledger(id,user_id,reference,kind,points_delta,frozen_delta,note,points_after,frozen_after) VALUES('decimal','p','decimal-charge','charge',0,-4.75,'fractional',5.25,0)")
        row=account.legacy_ledger('u')[0]
        self.assertEqual((row['amount'],row['available_delta'],row['frozen_delta']),(-475,0,-475))
        self.assertEqual(account.legacy_spend('u'),475)
        self.assertEqual(db.scalar("SELECT balance FROM users WHERE id='u'"),1000)

    def test_historical_ledger_is_visible_without_crediting_wallet_again(self):
        db.execute("INSERT INTO platform_ledger(id,user_id,reference,kind,points_delta,frozen_delta,note,points_after,frozen_after) VALUES('old','p','old-charge','charge',0,-5,'historical',15,0)")
        rows=account.legacy_ledger('u');self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['amount'],rows[0]['available_delta'],rows[0]['frozen_delta']),(-500,0,-500))
        self.assertTrue(rows[0]['legacy_registration'])
        self.assertEqual(account.legacy_spend('u'),500)
        self.assertEqual(account.legacy_ledger('v'),[])
        self.assertEqual(db.scalar("SELECT balance FROM users WHERE id='u'"),1000)
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)

    def test_subscription_finance_excludes_registration_credit(self):
        from backend.admin_content import finance_data
        db.execute("INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,business,description) VALUES('u','redeem',700,1700,300,'registration:code','registration','test'),('u','redeem',200,1900,300,'subscription:code','subscription','test')")
        report=finance_data(self.request)
        self.assertEqual(report['totals']['credited'],200)
        self.assertEqual(next(r for r in report['users'] if r['id']=='u')['credited'],200)

    def test_account_adjustment_cannot_spend_reserved_funds(self):
        from backend.wallet import credit
        with self.assertRaisesRegex(BusinessError,'冻结积分'):
            with db.transaction() as q:credit(q,self.request,'u','adjustment','test-adjust','test',-800)
        self.assertEqual(db.scalar("SELECT balance FROM users WHERE id='u'"),1000)
        with db.transaction() as q:credit(q,self.request,'u','adjustment','test-adjust','test',-700)
        self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),{'balance':300,'frozen':300})

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

    def test_historical_partner_rewards_keep_original_owner_after_identity_merge(self):
        from backend.invitations import listing
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code) VALUES('old','old@example.test','Old inviter','synthetic','old')")
        db.execute("UPDATE users SET invited_by='v' WHERE id='u'")
        db.execute("INSERT INTO platform_users(id,email,name,password_hash,xufu_user_id) VALUES('old-p','old-p@example.test','Old','unused','old')")
        db.execute("INSERT INTO platform_orders(id,user_id,quantity,need_oauth,unit_price,source,request_key,request_hash,template,attempt_limit,timeout_minutes) VALUES('o','p',1,false,5,'web','test','hash','{}',1,20)")
        db.execute("INSERT INTO platform_partner_rewards(order_id,invitee_id,inviter_id,rate_percent,fee,amount) VALUES('o','p','old-p',20,5,1)")
        self.assertEqual(listing(self.request,'old')['stats']['earned'],100)
        self.assertEqual(listing(self.request,'v')['stats']['earned'],0)

if __name__=='__main__':unittest.main()
