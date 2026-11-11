import unittest
from urllib.parse import urlsplit,unquote
from unittest.mock import patch
import test_registration_account as fixture
from backend import db,proxies,subscription,subscription_cslive_go,subscription_cslive_full
from backend.common import encrypt,BusinessError
from backend.manual_proxy_sessions import endpoint_for_order,session_minutes
from backend.seed import DEFAULTS


class ManualSessionFormatTests(unittest.TestCase):
    def resource(self,vendor,username='fixture',password='test-password',minutes=30,mode='dynamic'):
        return dict(vendor=vendor,ip_mode=mode,session_minutes=minutes,endpoint_enc=encrypt(f'http://{username}:{password}@gateway.example.test:3000'))

    def test_each_supplier_replaces_country_session_and_duration(self):
        for vendor in ('cliproxy','1024proxy','711proxy','iproyal'):
            username='fixture-region-PH-sid-old-t-5' if vendor in ('cliproxy','1024proxy') else 'fixture-zone-custom-region-PH-session-12345678-sessTime-5-sessAuto-1' if vendor=='711proxy' else 'fixture'
            password='test_country-ph_session-old_lifetime-1h' if vendor=='iproyal' else 'test-password'
            row=self.resource(vendor,username,password)
            urls=[endpoint_for_order(row,'US')[0] for _ in range(60)]
            self.assertEqual(len(set(urls)),60)
            for url in urls:
                parsed=urlsplit(url);user,pw=unquote(parsed.username),unquote(parsed.password)
                self.assertEqual(parsed.hostname,'gateway.example.test');self.assertEqual(parsed.port,3000)
                if vendor=='iproyal':
                    self.assertIn('_country-us_',pw);self.assertIn('_lifetime-30m',pw);self.assertNotIn('_country-ph',pw)
                else:
                    self.assertEqual(pw,'test-password');self.assertEqual(user.count('-region-'),1);self.assertIn('-region-US-',user);self.assertNotIn('-region-PH',user)
                    if vendor=='711proxy':self.assertRegex(user,r'-session-\d{8}-sessTime-30-sessAuto-1$')
                    else:self.assertRegex(user,r'-sid-[a-zA-Z0-9]{8}-t-30$')
            self.assertEqual(endpoint_for_order(row,'US')[1],1800)

    def test_saved_duration_static_and_custom_templates(self):
        row=self.resource('1024proxy','fixture-region-PH-sid-old-t-5',minutes=None)
        self.assertEqual(session_minutes(row),5)
        self.assertEqual(endpoint_for_order(row,'US')[1],300)
        row=self.resource('iproyal',password='test_country-ph_lifetime-2h',minutes=None)
        self.assertEqual(session_minutes(row),120)
        row=self.resource('other','fixture-{country}-{session}-{minutes}')
        url,seconds=endpoint_for_order(row,'JP');self.assertIn('fixture-JP-',url);self.assertTrue(urlsplit(url).username.endswith('-30'))
        with self.assertRaises(BusinessError):endpoint_for_order(self.resource('other'),'US')
        row=self.resource('1024proxy',mode='static');self.assertEqual(endpoint_for_order(row,'US')[0],'http://fixture:test-password@gateway.example.test:3000')
        for vendor,minutes in [('1024proxy',121),('711proxy',4),('711proxy',181),('iproyal',0),('cliproxy',True)]:
            with self.assertRaises(BusinessError):endpoint_for_order(self.resource(vendor,minutes=minutes),'US')


class ManualSessionBindingTests(unittest.TestCase):
    tearDown=fixture.RegistrationAccountTests.tearDown

    def setUp(self):
        fixture.RegistrationAccountTests.setUp(self)
        for key,value in DEFAULTS.items():db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        db.execute("INSERT INTO products(id,name,description,fee) VALUES('plus','Plus','fixture',300)")
        self.request.state.user['role']='admin'

    def test_all_flows_use_order_country_and_keep_original_session_after_edit(self):
        value=dict(name='fixture',country='PH',endpoint='http://fixture-region-PH-sid-old-t-5:password@gateway.example.test:3000',vendor='1024proxy',ip_mode='dynamic',session_minutes=20,enabled=True,capacity=10,weight=1)
        id=proxies.save_resource_value(value,self.request)['id']
        for index,module in enumerate((subscription,subscription_cslive_go,subscription_cslive_full)):
            order=f'order-{index}'
            db.execute("INSERT INTO orders(id,user_id,product_id,product_name,email,country,status,fee,idempotency_key,request_hash,workflow) VALUES($1,'u','plus','Plus','fixture@example.test','US','processing',300,$1,'fixture','subscription')",order)
            db.execute("INSERT INTO subscription_runs(order_id,user_id,subscription_flow) VALUES($1,'u',$2)",order,module.FLOW)
            endpoint=module._acquire_order_proxy(order,'US',7200)
            self.assertIn('-region-US-',endpoint);self.assertIn('-t-20:',endpoint)
            before=db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1',order)
            proxies.save_resource_value(dict(value,session_minutes=40),self.request,id)
            self.assertEqual(module._acquire_order_proxy(order,'US',7200),endpoint)
            self.assertEqual(before,db.one('SELECT * FROM order_proxy_sessions WHERE order_id=$1',order))
            proxies.save_resource_value(value,self.request,id)
        self.assertEqual(db.scalar('SELECT count(DISTINCT endpoint_enc) FROM order_proxy_sessions'),3)
        self.assertEqual(next(r for r in proxies.resources()['items'] if r['id']==id)['session_minutes'],20)

    def test_711_session_collision_regenerates_before_binding(self):
        value=dict(name='fixture',country='PH',endpoint='http://fixture-zone-custom-region-PH-session-12345678-sessTime-5-sessAuto-1:password@gateway.example.test:10000',vendor='711proxy',ip_mode='dynamic',session_minutes=10,enabled=True,capacity=10,weight=1)
        proxies.save_resource_value(value,self.request)
        with patch('backend.manual_proxy_sessions.secrets.randbelow',side_effect=[1]*16+[2]*8):
            for order in ('one','two'):
                db.execute("INSERT INTO orders(id,user_id,product_id,product_name,email,country,status,fee,idempotency_key,request_hash) VALUES($1,'u','plus','Plus','fixture@example.test','US','processing',300,$1,'fixture')",order)
                subscription._acquire_order_proxy(order,'US',600)
        self.assertEqual({r['session_ref'] for r in db.all('SELECT session_ref FROM order_proxy_sessions')},{'11111111','22222222'})
