import unittest
from unittest.mock import patch
from copy import deepcopy
import test_registration_account as fixture
from backend import db, regions, country_pool, price_reference, records
from backend.common import encrypt
from backend.seed import DEFAULTS


class ProxyRegionVisibilityTests(unittest.TestCase):
    tearDown=fixture.RegistrationAccountTests.tearDown

    def setUp(self):
        fixture.RegistrationAccountTests.setUp(self)
        for key,value in DEFAULTS.items():
            db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        rows=[r for r in country_pool.COUNTRY_REFERENCE.values() if r.get('currency')][:211]
        self.catalog=[{**r,'enabled':i%2==0,'sort':i} for i,r in enumerate(rows)]
        db.execute("UPDATE settings SET value=$1 WHERE key='regions'",{'items':self.catalog})
        db.execute("UPDATE settings SET value=value||$1::jsonb WHERE key='payment'",{'subscription_flow':'cslive_go_direct_pay'})
        self.assertEqual(len(self.catalog),211)

    def test_three_modes_keep_211_price_regions_and_separate_payment_choices(self):
        original=db.scalar("SELECT value FROM settings WHERE key='regions'")
        pulled=[self.catalog[1],self.catalog[3]]
        for row in pulled:
            db.execute('INSERT INTO subscription_country_entries(country,id,endpoint_enc,session_minutes) VALUES($1,$1,$2,10)',row['code'],encrypt('http://fixture.invalid:8080'))
        db.execute("INSERT INTO subscription_country_entries(country,id,endpoint_enc,session_minutes) VALUES('GLOBAL','global',$1,10)",encrypt('http://global.invalid:8080'))
        for mode in ('manual','country_pool','national_pool','manual'):
            with self.subTest(mode=mode):
                db.execute("UPDATE settings SET value=value||$1::jsonb WHERE key='proxy'",{'mode':mode})
                expected={r['code'] for r in (pulled if mode=='national_pool' else self.catalog) if mode=='national_pool' or r['enabled']}
                self.assertEqual({r['code'] for r in regions.enabled()},expected)
                self.assertEqual({r['code'] for r in records.catalog(self.request)['countries']},expected)
                data=price_reference.customer_prices(self.request)
                self.assertEqual(len(data['countries']),211)
                self.assertEqual(data['rows'],[])
                self.assertEqual(db.scalar("SELECT value FROM settings WHERE key='regions'"),original)

    def test_sync_does_not_add_remove_or_change_existing_regions(self):
        original=db.scalar("SELECT value FROM settings WHERE key='regions'")
        with patch.object(country_pool,'countries',return_value=[{'code':'US','name':'New name','currency':'USD'}]):
            country_pool.sync()
        self.assertEqual(db.scalar("SELECT value FROM settings WHERE key='regions'"),original)
        self.assertEqual(len(regions.items()),211)

    def test_region_display_can_be_enabled_without_proxy_inventory(self):
        self.request.state.user['role']='admin'
        for mode in ('country_pool','national_pool'):
            db.execute("UPDATE settings SET value=value||$1::jsonb WHERE key='proxy'",{'mode':mode})
            row=deepcopy(self.catalog[1]);row['enabled']=True
            regions.save_value(row,self.request,row['code'])
            self.assertTrue(next(r for r in regions.items() if r['code']==row['code'])['enabled'])
