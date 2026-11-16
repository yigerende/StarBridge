"""Credential reads use an isolated database; no real accounts or upstream calls."""
import unittest
from fastapi.testclient import TestClient
from backend import db
from backend.common import digest
from backend.main import app
from backend.refund_mother_upstream import seal_secret
import test_refunds as fixture


class CredentialTests(unittest.TestCase):
    tearDown=fixture.RefundTests.tearDown

    def setUp(self):
        fixture.RefundTests.setUp(self)
        self.value={'email':'target@example.test','password':'fixture-password <&> 中文',
                    'totp_secret':'JBSWY3DPEHPK3PXP','internal_extra':'must-not-leak'}
        db.execute("INSERT INTO refunds(id,user_id,email,credentials,fee,status,fee_status) VALUES('credential-order','u',$1,$2,0,'succeeded','settled')",
                   self.value['email'],seal_secret('credential-order',self.value))
        self.client=TestClient(app,cookies={'xupay_session':'test-session-a'})
        self.path='/api/admin/refund/orders/credential-order/credentials'

    def test_admin_reads_existing_encrypted_order_without_mutations_and_audits(self):
        before=db.one("SELECT * FROM refunds WHERE id='credential-order'")
        wallets=db.all('SELECT id,balance,frozen FROM users ORDER BY id')
        response=self.client.get(self.path)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json(),{k:self.value[k] for k in ('email','password','totp_secret')})
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertNotIn('must-not-leak',response.text)
        self.assertEqual(db.one("SELECT * FROM refunds WHERE id='credential-order'"),before)
        self.assertEqual(db.all('SELECT id,balance,frozen FROM users ORDER BY id'),wallets)
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)
        event=db.one("SELECT actor_id,action,target,detail FROM audit_events WHERE action='refund.order.credentials'")
        self.assertEqual(event['actor_id'],'a');self.assertEqual(event['target'],'credential-order')
        self.assertNotIn(self.value['password'],event['detail']);self.assertNotIn(self.value['totp_secret'],event['detail'])

    def test_non_admin_and_all_api_keys_cannot_read_or_trigger_audit(self):
        db.execute("INSERT INTO api_keys(id,user_id,name,hash,prefix) VALUES('admin-key','a','Fixture',$1,'fixture')",digest('fixture-admin-api-key'))
        for headers,status in [({},401),({'Cookie':'xupay_session=test-session-u'},403),({'Cookie':'xupay_session=test-session-v'},403),
                               ({'Authorization':'Bearer fixture-api-key-long-enough'},403),({'Authorization':'Bearer fixture-admin-api-key'},403)]:
            response=TestClient(app).get(self.path,headers=headers)
            self.assertEqual(response.status_code,status,response.text)
            self.assertNotIn(self.value['password'],response.text)
            self.assertNotIn(self.value['totp_secret'],response.text)
        self.assertEqual(db.scalar("SELECT count(*) FROM audit_events WHERE action='refund.order.credentials'"),0)
        for prefix in ('/api/refund/orders/','/api/v1/refund/orders/'):
            response=self.client.get(prefix+'credential-order/credentials')
            self.assertEqual(response.status_code,404)
        spec=self.client.get('/api/openapi.json').json()
        self.assertNotIn(self.path,spec['paths'])

    def test_missing_corrupt_or_mismatched_record_is_not_disclosed(self):
        self.assertEqual(self.client.get(self.path.replace('credential-order','missing')).status_code,404)
        samples=[b'bad',seal_secret('another-order',self.value),seal_secret('credential-order',{**self.value,'email':'other@example.test'}),
                 seal_secret('credential-order',{'email':self.value['email'],'password':123,'totp_secret':'fixture'})]
        for secret in samples:
            db.execute("UPDATE refunds SET credentials=$1 WHERE id='credential-order'",secret)
            response=self.client.get(self.path)
            self.assertEqual(response.status_code,409,response.text)
            self.assertNotIn(self.value['password'],response.text)
            self.assertNotIn(self.value['totp_secret'],response.text)
        self.assertEqual(db.scalar("SELECT count(*) FROM audit_events WHERE action='refund.order.credentials'"),0)


if __name__=='__main__':unittest.main()
