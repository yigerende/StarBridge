import json,subprocess,unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import test_registration_account as fixture
from backend import db,membership,refunds
from backend.common import digest
from backend.main import app
from backend.seed import DEFAULTS


class MembershipServiceTests(unittest.TestCase):
    tearDown=fixture.RegistrationAccountTests.tearDown

    def setUp(self):
        fixture.RegistrationAccountTests.setUp(self)
        for key,value in DEFAULTS.items():db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        db.execute("UPDATE settings SET value=value||'{\"subscription_flow\":\"cslive_go_direct_pay\"}'::jsonb WHERE key='payment'")
        db.execute("UPDATE settings SET value=$1 WHERE key='tiers'",{'items':[{'code':'base','name':'普通','threshold':0,'discount':100},{'code':'gold','name':'金阶','threshold':1000,'discount':90},{'code':'diamond','name':'钻阶','threshold':2000,'discount':80}]})
        db.execute("INSERT INTO products(id,name,description,fee) VALUES('plus','Plus','fixture',1000)")
        db.execute("UPDATE platform_settings SET value=value||$1::jsonb WHERE id=1",{'price_standard':5,'price_oauth':8,'orders_open':True})
        db.execute('UPDATE refund_settings SET service_fee=500,accepting=true WHERE id')
        with db.transaction() as q:membership.assign_membership(q,['u'],'gold',None)
        db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,'u',now()+interval '1 hour')",digest('member-fixture'))

    def test_all_service_quotes_match_actual_pricing_after_growth_upgrade(self):
        db.execute("INSERT INTO orders(id,user_id,product_id,product_name,email,status,fee,idempotency_key,request_hash) VALUES('new','u','plus','Plus','u@example.test','succeeded',1000,'new','fixture')")
        db.execute("INSERT INTO ledger(user_id,order_id,kind,amount,balance_after,frozen_after,reference,description) VALUES('u','new','charge',-1000,1000,300,'new','fixture')")
        db.execute("INSERT INTO user_service_prices(user_id,service,fee) VALUES('u','refund',0)")
        with patch('backend.registration_gateway.enabled',return_value=True):result=membership.membership(self.request)
        self.assertEqual(result['membership']['current']['code'],'diamond')
        services=result['services'];self.assertEqual([r['business'] for r in services],['registration','registration','subscription','refund'])
        self.assertEqual([r['member_fee'] for r in services],[400,800,800,0])
        self.assertEqual(services[-1]['member_fee'],refunds.pricing({'id':'u'})['fee'])
        self.assertFalse(services[1]['membership_eligible']);self.assertEqual(services[-1]['price_source'],'account_override')
        source="""const {Pool}=require('pg');const {prices}=require('./integration/pricing');const db=new Pool({connectionString:process.env.DATABASE_URL});(async()=>{try{const s=(await db.query('SELECT value FROM platform_settings WHERE id=1')).rows[0].value;console.log(JSON.stringify(await prices(db,'p',s)))}finally{await db.end()}})().catch(e=>{console.error(e.message);process.exitCode=1})"""
        actual=json.loads(subprocess.check_output(['node','-e',source],cwd=db.ROOT/'services/gpt-register',timeout=20))
        self.assertEqual(actual['membership']['current']['code'],'diamond')
        self.assertEqual([round(p['unit_price']*100) for p in actual['products']],[400,800])
        db.execute("UPDATE settings SET value=value||'{\"register\":false,\"refund\":false}'::jsonb WHERE key='features'")
        with patch('backend.registration_gateway.enabled',return_value=True):services=membership.membership(self.request)['services']
        self.assertFalse(services[0]['enabled']);self.assertFalse(services[-1]['enabled'])

    def test_member_cannot_read_or_operate_updater_or_get_build_metadata(self):
        with patch('backend.main.worker_loop'),TestClient(app,raise_server_exceptions=False) as client:
            self.assertEqual(client.get('/api/admin/system/update').status_code,401)
            client.cookies.set('xupay_session','member-fixture')
            self.assertEqual(client.get('/api/admin/system/update').status_code,403)
            for action in ('check','prepare','restart','rollback'):
                self.assertEqual(client.post('/api/admin/system/update/'+action,json={}).status_code,403)
            for path in ('/api/public','/api/me','/health/live'):
                response=client.get(path);self.assertEqual(response.status_code,200)
                for secret in ('current_version','latest_version','repository_url','rollback_versions'):
                    self.assertNotIn(secret,response.text)
            self.assertEqual(client.get('/api/membership').status_code,200)
