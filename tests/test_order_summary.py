"""Order-center statistics use the full filtered set, in a disposable database."""
import json
import os
import subprocess
import unittest

import test_registration_account as fixture
from test_registration_account import ROOT
from backend import db
from backend.records import admin_orders


class OrderSummaryTests(unittest.TestCase):
    tearDown = fixture.RegistrationAccountTests.tearDown

    def setUp(self):
        fixture.RegistrationAccountTests.setUp(self)
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code,role) VALUES('admin','admin@test.example','Admin','synthetic','admin','admin')")
        db.execute("INSERT INTO platform_users(id,email,name,password_hash,xufu_user_id) VALUES('pv','v@reg.example','Other','unused','v'),('pa','admin@reg.example','Admin','unused','admin')")
        db.execute("INSERT INTO products(id,name,description,fee) VALUES('plus','Plus','test',500)")

    def sub(self, id, status='queued', owner='u', activation='active', renewal='disabled', workflow='subscription'):
        db.execute("""INSERT INTO orders(id,user_id,product_id,product_name,email,status,fee,idempotency_key,request_hash,workflow,activation,renewal)
            VALUES($1,$2,'plus','Plus','subscription@test.example',$3,500,$1,'test',$4,$5,$6)""",id,owner,status,workflow,activation,renewal)

    def reg(self, id, status='queued', owner='p', oauth=False):
        db.execute("""INSERT INTO platform_orders(id,user_id,quantity,need_oauth,unit_price,status,source,request_key,request_hash,template,attempt_limit,timeout_minutes)
            VALUES($1,$2,3,$3,5,$4,'web',$1,'test','{}',1,20)""",id,owner,oauth,status)

    def registrations(self, queries):
        script="""const {Database}=require('./src/platform/db');
const {listOrders}=require('./src/platform/reports');
const db=new Database(process.env.DATABASE_URL);
(async()=>{try{const result=[];for(const [owner,query] of JSON.parse(process.argv[1]))result.push(await listOrders(db,owner,query));process.stdout.write(JSON.stringify(result))}finally{await db.close()}})().catch(e=>{console.error(e);process.exit(1)});"""
        result=subprocess.run(['node','-e',script,json.dumps(queries)],cwd=ROOT/'services/gpt-register',env={**os.environ,'XUFU_INTEGRATION':'1'},capture_output=True,text=True,check=True,timeout=30)
        return json.loads(result.stdout)

    def test_subscription_counts_all_pages_and_payment_followup_separately(self):
        for i in range(105):self.sub('queue-'+str(i))
        self.sub('running','processing');self.sub('unknown','review')
        self.sub('activation','succeeded',activation='pending')
        self.sub('renewal','succeeded',renewal='retrying')
        self.sub('paid-review','succeeded',activation='review',renewal='pending')
        self.sub('done','succeeded');self.sub('link','awaiting_payment',workflow='link')
        self.sub('failed','failed');self.sub('cancelled','cancelled')
        self.sub('deleted');db.execute("UPDATE orders SET deleted_at=now() WHERE id='deleted'")
        self.sub('admin',owner='admin')
        expected=dict(queued=105,running=3,review=1,followup=1,completed=2,closed=2)
        first=admin_orders(include_admin=False,page=1,page_size=50)
        last=admin_orders(include_admin=False,page=3,page_size=50)
        self.assertEqual(first['summary'],expected)
        self.assertEqual(last['summary'],expected)
        self.assertEqual(first['total'],114)
        self.assertEqual(len(first['items']),50)
        self.assertEqual(len(last['items']),14)
        self.assertEqual(admin_orders(page=1,page_size=1)['summary']['queued'],106)
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)

    def test_subscription_filters_apply_to_summary_and_items(self):
        self.sub('match-q');self.sub('match-review','review');self.sub('other')
        self.sub('paid','succeeded',activation='review')
        self.sub('pending','succeeded',activation='pending')
        self.sub('admin-match','review',owner='admin')
        result=admin_orders(q='match',status='review',include_admin=False,page=1,page_size=1)
        self.assertEqual(result['total'],1)
        self.assertEqual(result['summary'],dict(queued=0,running=0,review=1,followup=0,completed=0,closed=0))
        self.assertEqual(result['items'][0]['id'],'match-review')
        followup=admin_orders(attention='followup',page=1,page_size=1)
        self.assertEqual(followup['summary']['followup'],1)
        self.assertEqual(followup['summary']['completed'],0)
        pending=admin_orders(attention='pending',page=1,page_size=1)
        self.assertEqual(pending['total'],3)
        self.assertEqual(pending['summary']['running'],1)
        empty=admin_orders(q='no matches',page=1,page_size=1)
        self.assertEqual(empty['total'],0)
        self.assertTrue(all(value==0 for value in empty['summary'].values()))

    def test_registration_counts_all_pages_partial_delivery_and_filters(self):
        for i in range(105):self.reg('queue-'+str(i))
        for status in ('running','review','completed','partial','failed','cancelled'):self.reg('reg-'+status,status,oauth=True)
        self.reg('admin','queued','pa');self.reg('other','completed','pv')
        first,last,filtered,matched,all_rows,empty=self.registrations([
            [None,dict(limit=50,include_admin='false')],
            [None,dict(limit=50,offset=100,include_admin='false')],
            [None,dict(mode='oauth',status='partial',limit=1)],
            [None,dict(q='queue-',include_admin='false',limit=1)],
            [None,dict(limit=1)], [None,dict(q='nothing')],
        ])
        expected=dict(queued=105,running=1,review=1,completed=2,partial=1,closed=2)
        self.assertEqual(first['summary'],expected)
        self.assertEqual(last['summary'],expected)
        self.assertEqual(first['total'],112)
        self.assertEqual(len(last['items']),12)
        self.assertEqual(filtered['summary']['partial'],1)
        self.assertEqual(filtered['summary']['completed'],0)
        self.assertEqual(filtered['total'],1)
        self.assertEqual(matched['total'],105)
        self.assertEqual(matched['summary']['queued'],105)
        self.assertEqual(all_rows['summary']['queued'],106)
        self.assertEqual(empty['total'],0)
        self.assertTrue(all(value==0 for value in empty['summary'].values()))

    def test_registration_customer_contract_and_owner_scope_unchanged(self):
        self.reg('mine','partial');self.reg('other','completed','pv');self.reg('admin','queued','pa')
        mine,other=self.registrations([['p',{}],['pv',{}]])
        self.assertNotIn('summary',mine)
        self.assertEqual(mine['total'],1)
        self.assertEqual([row['id'] for row in mine['items']],['mine'])
        self.assertEqual([row['id'] for row in other['items']],['other'])
        self.assertNotIn('email',mine['items'][0])


if __name__=='__main__':unittest.main()
