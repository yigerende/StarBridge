"""Private bridge integration using synthetic proxies and a disposable database."""
import asyncio
import unittest

import httpx
import test_refunds as fixture
from backend import db, refunds
from backend.main import app
from backend.refund_mother_upstream import open_secret


class RefundChildSessionTests(unittest.TestCase):
    setUp = fixture.RefundTests.setUp
    tearDown = fixture.RefundTests.tearDown

    def test_supplier_settings_roundtrip_permissions_and_secret_preservation(self):
        async def scenario():
            await refunds.start()
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as c:
                    admin = {'Cookie': 'xupay_session=test-session-a'}
                    user = {'Cookie': 'xupay_session=test-session-u'}
                    path = '/api/admin/refund/proxies'
                    source = 'http://synthetic:private-fixture@gateway.invalid:8080'
                    for vendor in ('iproyal', 'cliproxy', '1024proxy', '711proxy'):
                        data = dict(name=vendor, vendor=vendor, country='ph', session_minutes=60,
                                    target='child', enabled=True, url=source)
                        for headers, expected in [({}, 401), (user, 403)]:
                            self.assertEqual((await c.post(path, headers=headers, json=data)).status_code, expected)
                        r = await c.post(path, headers=admin, json=data)
                        self.assertEqual(r.status_code, 200, r.text)
                    r = await c.get('/api/admin/refund/resources/proxies', headers=admin)
                    self.assertEqual(r.status_code, 200, r.text)
                    rows = r.json()['items']
                    self.assertEqual(len(rows), 4)
                    self.assertNotIn('private-fixture', r.text)
                    self.assertNotIn('gateway.invalid', r.text)
                    for row in rows:
                        self.assertEqual((row['country'], row['session_minutes']), ('PH', 60))
                        data = {k: row[k] for k in ('id', 'name', 'target', 'enabled', 'vendor', 'country', 'session_minutes')}
                        data.update(url='', country='US', session_minutes=30)
                        r = await c.post(path, headers=admin, json=data)
                        self.assertEqual(r.status_code, 200, r.text)
                        stored = db.one('SELECT vendor,country,session_minutes,secret FROM refund_proxies WHERE id=$1', row['id'])
                        self.assertEqual((stored['vendor'], stored['country'], stored['session_minutes']), (row['vendor'], 'US', 30))
                        self.assertEqual(open_secret('proxy:'+row['id'], stored['secret']), source)
                        # Invalid metadata must not partially update an existing resource.
                        for changes in ({'country':'Philippines'}, {'session_minutes':-1}, {'vendor':'unknown'}):
                            self.assertEqual((await c.post(path, headers=admin, json={**data, **changes})).status_code, 400)
                        self.assertEqual(db.scalar('SELECT country FROM refund_proxies WHERE id=$1', row['id']), 'US')
                    self.assertEqual(db.scalar('SELECT count(*) FROM refund_child_proxy_sessions'), 0)
                    self.assertEqual(db.scalar('SELECT count(*) FROM refunds'), 0)
                    self.assertEqual(db.scalar('SELECT count(*) FROM ledger'), 0)
            finally:
                await refunds.stop()
        asyncio.run(scenario())
