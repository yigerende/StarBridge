"""Account-specific prices on an isolated database, without running workers."""
import unittest
from fastapi.testclient import TestClient
from test_registration_account import RegistrationAccountTests as Fixture
from backend import db, membership, refunds
from backend.admin_accounts import account_options
from backend.common import BusinessError, digest
from backend.main import app
from backend.seed import DEFAULTS
from backend.settings import save_settings_value, validate_config


class ExclusivePricingTests(unittest.TestCase):
    tearDown=Fixture.tearDown

    def setUp(self):
        Fixture.setUp(self)
        for key,value in DEFAULTS.items():
            db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        db.execute("UPDATE settings SET value=value||'{\"subscription_flow\":\"cslive_go_direct_pay\"}'::jsonb WHERE key='payment'")
        db.execute("INSERT INTO products(id,name,description,fee) VALUES('plus','Plus','',1000),('pro','Pro','',2000)")
        db.execute("UPDATE users SET membership_code='L9',fee_override=777 WHERE id='u'")

    def save(self, prices):
        with db.transaction() as q:
            account_options(q,self.request,'u',prices)

    def test_business_prices_override_discount_independently_including_zero_and_above_standard(self):
        self.save({'service_prices':{'registration_standard':0,'registration_oauth':987,'refund':1234},'product_prices':{'plus':1500}})
        m=membership.membership_for('u')
        self.assertEqual(membership.fee_for(m,500,'registration_standard'),0)
        self.assertEqual(membership.fee_for(m,800,'registration_oauth'),987)
        self.assertEqual(membership.fee_for(m,1000,'plus'),1500)
        self.assertEqual(membership.fee_for(m,2000,'pro'),777)
        self.assertEqual(refunds.pricing({'id':'u'})['fee'],1234)
        other=membership.membership_for('v')
        self.assertEqual(membership.fee_for(other,500,'registration_standard'),500)
        self.assertEqual(membership.fee_source(other,'refund'),'membership')
        self.assertEqual(membership.fee_source(m,'registration_standard'),'account_override')

    def test_clearing_only_service_prices_restores_membership_and_preserves_subscription(self):
        self.save({'service_prices':{'refund':0},'product_prices':{'plus':0}})
        self.save({'service_prices':{}})
        m=membership.membership_for('u')
        self.assertEqual(membership.fee_for(m,1000,'refund'),200)
        self.assertEqual(membership.fee_source(m,'refund'),'membership')
        self.assertEqual(membership.fee_for(m,500,'registration_standard'),100)
        self.assertEqual(membership.fee_for(m,1000,'plus'),0)
        self.assertEqual(membership.fee_for(m,2000,'pro'),777)

    def test_membership_eligibility_per_service_and_exclusive_price_precedence(self):
        db.execute("UPDATE users SET fee_override=NULL WHERE id='u'")
        m=membership.membership_for('u')
        self.assertEqual(membership.fee_for(m,500,'registration_standard'),100)
        self.assertEqual(membership.fee_for(m,800,'registration_oauth'),800)
        self.assertEqual(membership.fee_source(m,'registration_oauth'),'standard')
        rules={'registration_standard':False,'registration_oauth':True,'refund':False,'subscription':{'plus':False,'pro20x':True}}
        save_settings_value('membership_pricing',rules,self.request)
        m=membership.membership_for('u')
        self.assertEqual(membership.fee_for(m,500,'registration_standard'),500)
        self.assertEqual(membership.fee_for(m,800,'registration_oauth'),160)
        self.assertEqual(membership.fee_for(m,1000,'plus'),1000)
        self.assertEqual(membership.fee_for(m,2000,'pro'),400)
        self.assertEqual(membership.fee_for(m,500,'refund'),500)
        self.save({'service_prices':{'registration_standard':0,'refund':123},'product_prices':{'plus':1500}})
        m=membership.membership_for('u')
        self.assertEqual(membership.fee_for(m,500,'registration_standard'),0)
        self.assertEqual(membership.fee_for(m,500,'refund'),123)
        self.assertEqual(membership.fee_for(m,1000,'plus'),1500)
        self.assertFalse(membership.membership_eligible(m,'plus'))
        self.assertEqual(membership.fee_source(m,'plus'),'product_override')
        for invalid in ({**rules,'refund':0},{**rules,'subscription':{'unknown':False}},{**rules,'subscription':{'plus':'false'}}):
            with self.assertRaises(BusinessError):save_settings_value('membership_pricing',invalid,self.request)
        self.assertEqual(db.scalar("SELECT value FROM settings WHERE key='membership_pricing'"),rules)

    def test_invalid_prices_roll_back_all_changes(self):
        self.save({'service_prices':{'refund':123}})
        for prices in ({'refund':-1},{'refund':True},{'refund':1.5},{'refund':100000001},{'typo':0},[],None):
            with self.assertRaises(BusinessError):self.save({'service_prices':prices})
            self.assertEqual(db.scalar("SELECT fee FROM user_service_prices WHERE user_id='u'"),123)

    def test_custom_tier_identifier_renames_assigned_accounts_atomically(self):
        tiers=db.scalar("SELECT value FROM settings WHERE key='tiers'")
        for tier in tiers['items']:
            tier['original_code']=tier['code']
            if tier['code']=='L9':tier.update(code='LMax',name='董事')
        save_settings_value('tiers',tiers,self.request)
        self.assertEqual(db.scalar("SELECT membership_code FROM users WHERE id='u'"),'LMax')
        m=membership.membership_for('u')
        self.assertEqual((m['current']['name'],m['current']['discount'],m['source']),('董事',20,'manual'))
        self.assertTrue(all('original_code' not in t for t in db.scalar("SELECT value FROM settings WHERE key='tiers'")['items']))
        bad={'items':[{'code':'中文VIP-1','name':'自定义','threshold':0,'discount':100}]}
        validate_config('tiers',bad)
        for code in ('','a b','<script>','a'*33):
            with self.assertRaises(BusinessError):validate_config('tiers',{'items':[dict(bad['items'][0],code=code)]})
        # Removing an assigned tier still fails and rolls back the whole edit.
        with self.assertRaises(BusinessError):save_settings_value('tiers',bad,self.request)
        self.assertEqual(db.scalar("SELECT membership_code FROM users WHERE id='u'"),'LMax')

    def test_account_api_is_admin_only_and_price_edit_does_not_reprice_existing_order(self):
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code,role) VALUES('admin','admin@example.test','Admin','unused','admin','admin')")
        for id in ('u','admin'):
            db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')",digest('pricing-'+id),id)
        db.execute("INSERT INTO orders(id,user_id,product_id,product_name,email,status,fee,idempotency_key,request_hash,workflow) VALUES('old','u','plus','Plus','old@test.example','queued',555,'old','old','subscription')")
        value={'email':'u@example.test','name':'User','role':'member','status':'active','fee_override':444,'service_prices':{'registration_standard':0,'refund':789},'product_prices':{'plus':321}}
        client=TestClient(app)
        try:
            self.assertEqual(client.patch('/api/admin/users/u',json=value).status_code,401)
            client.cookies.set('xupay_session','pricing-u')
            self.assertEqual(client.patch('/api/admin/users/u',json=value).status_code,403)
            client.cookies.set('xupay_session','pricing-admin')
            result=client.patch('/api/admin/users/u',json=value,headers={'Origin':'http://testserver'})
            self.assertEqual(result.status_code,200,result.text)
            row=next(r for r in client.get('/api/admin/users').json()['items'] if r['id']=='u')
            self.assertEqual(row['service_prices'],value['service_prices'])
            self.assertEqual(row['product_prices'],value['product_prices'])
            self.assertEqual(db.scalar("SELECT fee FROM orders WHERE id='old'"),555)
            self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)
        finally:
            client.close()
