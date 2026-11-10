"""Contact editing, validation and permissions in a disposable local database."""
import unittest
from fastapi.testclient import TestClient
import test_registration_account as fixture
from backend import db
from backend.common import BusinessError, config, digest
from backend.seed import DEFAULTS
from backend.support import contact_url


class SupportTests(unittest.TestCase):
    tearDown=fixture.RegistrationAccountTests.tearDown

    def setUp(self):
        fixture.RegistrationAccountTests.setUp(self)
        for key,value in DEFAULTS.items():
            db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code,role) VALUES('admin','admin@test.example','Admin','synthetic','admin','admin')")
        for user in ('u','admin'):
            db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')",digest('support-'+user),user)

    def test_save_is_admin_only_preserves_site_fields_and_is_public(self):
        from backend.main import app
        before=config('site')
        values={'support_url':'@test_support','telegram_group_url':'https://t.me/+synthetic_invite'}
        admin=TestClient(app,cookies={'xupay_session':'support-admin'})
        member=TestClient(app,cookies={'xupay_session':'support-u'})
        anonymous=TestClient(app)
        try:
            self.assertEqual(anonymous.patch('/api/admin/support',json=values).status_code,401)
            self.assertEqual(member.patch('/api/admin/support',json=values).status_code,403)
            self.assertEqual(config('site'),before)
            response=admin.patch('/api/admin/support',json=values)
            self.assertEqual(response.status_code,200,response.text)
            expected={**values,'support_url':'https://t.me/test_support'}
            self.assertEqual(response.json(),expected)
            self.assertEqual(config('site'),{**before,**expected})
            site=anonymous.get('/api/public').json()['site']
            self.assertEqual({k:site[k] for k in expected},expected)
            nav=member.get('/api/me').json()['navigation']
            self.assertTrue(any(n['path']=='/support' for n in nav))
            self.assertFalse(any(n['path'].startswith('/admin') for n in nav))
            self.assertEqual(db.scalar("SELECT count(*) FROM audit_events WHERE target='support'"),1)
            cleared=admin.patch('/api/admin/support',json={k:'' for k in values})
            self.assertEqual(cleared.status_code,200)
            self.assertEqual(config('site')['telegram_group_url'],'')
        finally:admin.close();member.close();anonymous.close()

    def test_validation_blocks_unsafe_links_and_invalid_payload_without_writes(self):
        from backend.main import app
        from backend.settings import validate_config
        before=config('site')
        client=TestClient(app,cookies={'xupay_session':'support-admin'})
        try:
            for field,value in [('support_url','javascript:alert(1)'),('support_url','//host.example/x'),('support_url','https://user:secret@host.example'),('support_url','https://example.test/\nname'),('support_url',None),('telegram_group_url','https://t.me.evil.test/group'),('telegram_group_url','https://t.me/'),('telegram_group_url','https://t.me:bad/group'),('telegram_group_url','http://t.me/group')]:
                payload={'support_url':'','telegram_group_url':'',field:value}
                response=client.patch('/api/admin/support',json=payload)
                self.assertEqual(response.status_code,400,(field,value,response.text))
                self.assertEqual(config('site'),before)
            self.assertEqual(client.patch('/api/admin/support',json={'support_url':'','telegram_group_url':'','registration_open':False}).status_code,400)
            with self.assertRaises(BusinessError):validate_config('site',{**before,'telegram_group_url':'javascript:alert(1)'})
            self.assertEqual(db.scalar("SELECT count(*) FROM audit_events WHERE target='support'"),0)
        finally:client.close()

    def test_accounts_invites_and_existing_support_websites(self):
        self.assertEqual(contact_url(' @test_support '),'https://t.me/test_support')
        self.assertEqual(contact_url('t.me/test_group',group=True),'https://t.me/test_group')
        self.assertEqual(contact_url('https://t.me/joinchat/synthetic',group=True),'https://t.me/joinchat/synthetic')
        self.assertEqual(contact_url('https://help.example.test/chat'),'https://help.example.test/chat')
        self.assertEqual(contact_url('',group=True),'')


if __name__=='__main__':unittest.main()
