"""One membership growth total, without changing wallet or historical orders."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import unittest
import test_registration_account as fixture
from backend import db,membership,admin_accounts
from backend.common import now
from backend.seed import DEFAULTS


class MembershipGrowthTests(unittest.TestCase):
    tearDown=fixture.RegistrationAccountTests.tearDown

    def setUp(self):
        fixture.RegistrationAccountTests.setUp(self)
        for key,value in DEFAULTS.items():db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        self.tiers=[{'code':'base','name':'普通','threshold':0,'discount':100},{'code':'gold','name':'金阶','threshold':1000,'discount':90},{'code':'diamond','name':'钻阶','threshold':2000,'discount':80}]
        db.execute("UPDATE settings SET value=$1 WHERE key='tiers'",{'items':self.tiers})
        db.execute("INSERT INTO products(id,name,description,fee) VALUES('plus','Plus','fixture',1000)")
        self.request.state.user={'id':'u','role':'admin'}

    def spend(self,id,amount):
        db.execute("INSERT INTO orders(id,user_id,product_id,product_name,email,status,fee,idempotency_key,request_hash) VALUES($1,'u','plus','Plus','u@example.test','succeeded',$2,$1,'fixture')",id,amount)
        db.execute("INSERT INTO ledger(user_id,order_id,kind,amount,balance_after,frozen_after,reference,description) VALUES('u',$1,'charge',-$2,1000,300,$1,'fixture')",id,amount)

    def grant(self,code='gold',until=None):
        with db.transaction() as q:membership.assign_membership(q,['u'],code,until)

    def test_grant_tops_up_then_continues_to_next_tier_and_quote(self):
        self.spend('before',400)
        money=db.one("SELECT balance,frozen FROM users WHERE id='u'");ledger=db.all('SELECT * FROM ledger')
        self.grant();m=membership.membership_for('u')
        self.assertEqual((m['spent'],m['settled_spent'],m['growth_credit'],m['remaining']),(1000,400,600,1000))
        self.assertEqual(m['current'],m['automatic']);self.assertEqual(m['next']['code'],'diamond')
        self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),money);self.assertEqual(db.all('SELECT * FROM ledger'),ledger)
        self.spend('after',1000);m=membership.membership_for('u')
        self.assertEqual((m['spent'],m['current']['code'],m['next']),(2000,'diamond',None))
        self.assertEqual(membership.fee_for(m,1000,'plus'),800)
        self.assertEqual(membership.fee_for(m,1000,'refund'),800)
        self.assertEqual(membership.fee_for(m,1000,'registration_standard'),800)
        self.assertEqual(membership.fee_for(m,1000,'registration_oauth'),1000)
        self.assertEqual(admin_accounts.users(tier='diamond')['items'][0]['id'],'u')
        self.grant();self.assertEqual(membership.membership_for('u')['spent'],2000)

    def test_refund_expiry_clear_and_existing_spend_use_one_total(self):
        self.spend('before',400);self.grant(until=now()+timedelta(days=1));self.spend('after',500)
        db.execute("INSERT INTO ledger(user_id,order_id,kind,amount,balance_after,frozen_after,reference,description) VALUES('u','after','refund',200,1000,300,'refund','fixture')")
        self.assertEqual(membership.membership_for('u')['spent'],1300)
        db.execute("UPDATE users SET membership_until=now()-interval '1 second' WHERE id='u'")
        self.assertEqual(membership.membership_for('u')['spent'],700)
        self.grant();self.assertEqual(membership.membership_for('u')['spent'],1000)
        self.grant('');self.assertEqual(membership.membership_for('u')['spent'],700)
        self.spend('more',1500);self.grant('gold')
        self.assertEqual(membership.membership_for('u')['current']['code'],'diamond')

    def test_concurrent_duplicate_grants_and_unrelated_account_save(self):
        self.spend('before',400)
        with ThreadPoolExecutor(max_workers=12) as pool:list(pool.map(lambda _:self.grant(),range(24)))
        self.assertEqual(db.scalar("SELECT membership_growth_credit FROM users WHERE id='u'"),600)
        self.spend('after',700)
        with db.transaction() as q:admin_accounts.account_options(q,self.request,'u',{'membership_code':'gold','membership_until':None,'service_prices':{'refund':0}})
        self.assertEqual(membership.membership_for('u')['spent'],1700)
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),2)

    def test_all_businesses_accumulate_and_refunds_reduce_only_original_order(self):
        self.grant()
        self.spend('same-id',200)
        db.execute("INSERT INTO refunds(id,user_id,email,credentials,status,fee,fee_status) VALUES('same-id','u','fixture@example.test',decode('00','hex'),'succeeded',700,'settled')")
        for business,column,kind,amount in [
            ('registration','registration_order_id','charge',-500),
            ('registration','registration_order_id','refund',100),
            ('refund','refund_order_id','charge',-700),
            ('refund','refund_order_id','refund',100),
        ]:
            db.execute(f"INSERT INTO ledger(user_id,{column},business,kind,amount,balance_after,frozen_after,reference,description) VALUES('u','same-id',$1,$2,$3,1000,300,$4,'fixture')",business,kind,amount,business+kind)
        money=db.one("SELECT balance,frozen FROM users WHERE id='u'")
        m=membership.membership_for('u')
        self.assertEqual((m['spent'],m['settled_spent'],m['growth_credit']),(2200,1200,1000))
        self.assertEqual(m['current']['code'],'diamond')
        self.assertEqual(admin_accounts.users(tier='diamond')['items'][0]['membership']['spent'],2200)
        page=membership.membership(self.request,page_size=10)
        self.assertEqual(page['total'],3)
        self.assertEqual({r['business']:r['spent'] for r in page['growth']},{'subscription':200,'registration':400,'refund':600})
        self.assertEqual(sum(r['spent'] for r in page['growth']),m['settled_spent'])
        self.assertEqual(membership.membership_for('v')['spent'],0)
        self.grant()
        self.assertEqual(membership.membership_for('u')['spent'],2200)
        self.assertEqual(db.one("SELECT balance,frozen FROM users WHERE id='u'"),money)

    def test_manual_grant_accounts_for_prior_registration_and_refund_consumption(self):
        for rid in ('before','after'):
            db.execute("INSERT INTO refunds(id,user_id,email,credentials,status,fee,fee_status) VALUES($1,'u','fixture@example.test',decode('00','hex'),'succeeded',350,'settled')",rid)
        for business,column,amount in [('registration','registration_order_id',250),('refund','refund_order_id',350)]:
            db.execute(f"INSERT INTO ledger(user_id,{column},business,kind,amount,balance_after,frozen_after,reference,description) VALUES('u','before',$1,'charge',-$2,1000,300,$1,'fixture')",business,amount)
        self.grant()
        m=membership.membership_for('u')
        self.assertEqual((m['spent'],m['settled_spent'],m['growth_credit']),(1000,600,400))
        db.execute("INSERT INTO ledger(user_id,refund_order_id,business,kind,amount,balance_after,frozen_after,reference,description) VALUES('u','after','refund','charge',-300,1000,300,'after','fixture')")
        self.assertEqual(membership.membership_for('u')['spent'],1300)

    def test_existing_grants_backfill_once_and_preserve_new_growth(self):
        self.spend('before',400)
        # Simulate the pre-upgrade schema inside this isolated test database.
        db.execute('ALTER TABLE users DROP COLUMN membership_growth_credit')
        db.execute("UPDATE users SET membership_code='gold' WHERE id='u'")
        db.initialize()
        self.assertEqual(membership.membership_for('u')['spent'],1000)
        self.spend('after',700);db.initialize()
        self.assertEqual(membership.membership_for('u')['spent'],1700)
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),2)
