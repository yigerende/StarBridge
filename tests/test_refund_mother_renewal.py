"""Isolated database and simulated billing responses; no live cancellations."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import db, refund_mothers as mothers
from backend.common import BusinessError, now
from backend.main import app
from backend.refund_mother_upstream import TeamClient, UpstreamError
from test_refund_mother_management import MotherTests, FakeClient, token


class RenewalTests(unittest.TestCase):
    setUp = MotherTests.setUp
    tearDown = MotherTests.tearDown
    member = MotherTests.member

    def simulate(self, statuses, outcome=None):
        calls = []
        def renewal(client, email):
            calls.append(('GET', client.proxy, client.team))
            value = statuses.pop(0) if len(statuses) > 1 else statuses[0]
            if isinstance(value, Exception):
                raise value
            return {'team_id': client.team, 'active': True, 'will_renew': value,
                    'active_until': '2026-12-10T10:19:27Z', 'checked_at': now().isoformat()}
        def request(client, method, path, payload):
            calls.append((method, path, payload))
            if outcome:
                raise outcome
            return {'success': True}
        self.addCleanup(patch.stopall)
        patch.object(FakeClient, 'renewal', renewal, create=True).start()
        patch.object(FakeClient, 'request', request).start()
        return calls

    def cancel(self):
        return self.client.post(self.root+'/mother/renewal/cancel', json={'team_id':'team-fixture'})

    def test_confirmed_cancel_is_idempotent_preserves_wallet_and_audits(self):
        calls = self.simulate([True, False])
        before = db.all('SELECT id,balance,frozen FROM users ORDER BY id')
        result = self.cancel()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['status'], 'disabled')
        self.assertEqual(result.json()['active_until'], '2026-12-10T10:19:27Z')
        self.assertFalse(result.json()['pending'])
        self.assertEqual(self.cancel().json()['status'], 'disabled')
        self.assertEqual([c for c in calls if c[0]=='POST'], [('POST','/subscriptions/cancel',{'account_id':'team-fixture'})])
        self.assertTrue(all(c[1]=='http://fixture.invalid:8080' for c in calls if c[0]=='GET'))
        self.assertEqual(db.all('SELECT id,balance,frozen FROM users ORDER BY id'), before)
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'), 0)
        self.assertEqual(db.scalar('SELECT count(*) FROM refunds'), 0)
        self.assertEqual(db.scalar('SELECT count(*) FROM refund_mother_leases'), 0)
        self.assertEqual(db.scalar("SELECT count(*) FROM audit_events WHERE action='refund.mother.renewal.cancel'"), 1)
        self.assertEqual(db.scalar("SELECT management_json->'renewal'->>'status' FROM refund_mothers WHERE id='mother'"), 'disabled')

    def test_timeout_queries_result_and_does_not_repeat_unknown_write(self):
        statuses = [True, UpstreamError('read offline')]
        calls = self.simulate(statuses, UpstreamError('write response lost'))
        result = self.cancel()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['status'], 'checking')
        statuses[:] = [True]
        self.assertEqual(self.cancel().json()['status'], 'checking')
        statuses[:] = [False]
        checked = self.client.post(self.root+'/mother/renewal/check', json={})
        self.assertEqual(checked.json()['status'], 'disabled')
        self.assertFalse(checked.json()['pending'])
        self.assertEqual(sum(c[0]=='POST' for c in calls), 1)

    def test_lost_response_but_cancelled_is_reported_success(self):
        calls = self.simulate([True, False], UpstreamError('lost', 500))
        self.assertEqual(self.cancel().json()['status'], 'disabled')
        self.assertEqual(sum(c[0]=='POST' for c in calls), 1)

    def test_no_active_subscription_does_not_submit_cancel(self):
        value={'team_id':'team-fixture','active':False,'will_renew':False,'checked_at':now().isoformat(),'active_until':None}
        with patch.object(FakeClient,'renewal',return_value=value,create=True), patch.object(FakeClient,'request') as write:
            response=self.cancel()
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['status'],'no_subscription')
            self.assertEqual(write.call_count,0)

    def test_definite_rejection_is_visible_and_allows_explicit_retry(self):
        calls = self.simulate([True], UpstreamError('forbidden', 403))
        result = self.cancel().json()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('HTTP 403', result['message'])
        self.assertFalse(result['pending'])
        self.cancel()
        self.assertEqual(sum(c[0]=='POST' for c in calls), 2)

    def test_admin_only_stale_team_and_busy_mother_never_mutate(self):
        calls = self.simulate([True, False])
        for suffix in ('check', 'cancel'):
            path = self.root+'/mother/renewal/'+suffix
            self.assertEqual(self.user.post(path,json={'team_id':'team-fixture'}).status_code,403)
            self.assertEqual(TestClient(app).post(path,json={}).status_code,401)
            self.assertIn(TestClient(app).post(path,headers={'Authorization':'Bearer fixture-api-key-long-enough'},json={}).status_code,(401,403))
        path = self.root+'/mother/renewal/cancel'
        self.assertEqual(self.client.post(path,json={}).status_code,400)
        self.assertEqual(self.client.post(path,json={'team_id':'other'}).status_code,409)
        with mothers.team_operation('mother'):
            self.assertEqual(self.cancel().status_code,409)
        db.execute("INSERT INTO refunds(id,user_id,email,credentials,fee,status) VALUES('busy','u','child@example.test',decode('00','hex'),10,'processing')")
        db.execute("INSERT INTO refund_execution(refund_id,mother_id) VALUES('busy','mother')")
        self.assertEqual(self.cancel().status_code,409)
        self.assertEqual(calls,[])

    def test_concurrent_clicks_share_team_lease(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def renewal(client,email):
            calls.append('read')
            if len(calls)==1:
                entered.set()
                self.assertTrue(release.wait(5))
            return {'team_id':client.team,'active':True,'will_renew':len(calls)==1,'checked_at':now().isoformat(),'active_until':None}
        with patch.object(FakeClient,'renewal',renewal,create=True), patch.object(FakeClient,'request',return_value={'success':True}) as write:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(self.cancel)
                try:
                    self.assertTrue(entered.wait(5))
                    second=self.cancel()
                    self.assertEqual(second.status_code,409,second.text)
                finally:
                    release.set()
                self.assertEqual(first.result().json()['status'],'disabled')
            self.assertEqual(write.call_count,1)


class RenewalParsingTests(unittest.TestCase):
    def test_identity_and_strict_status_use_target_team(self):
        client=TeamClient(token(),'team-fixture','http://fixture.invalid:8080')
        self.addCleanup(client.close)
        with patch.object(client,'request',side_effect=[{'email':'mother@example.test'},{'is_active':True,'will_renew':False,'account_id':'team-fixture','active_until':'2026-12-10T10:19:27Z'}]) as req:
            result=client.renewal('mother@example.test')
            self.assertFalse(result['will_renew'])
            self.assertEqual(req.call_args.args,('GET','/subscriptions?account_id=team-fixture'))
        for data in ({'is_active':True},{'is_active':True,'will_renew':'false'},{'is_active':True,'will_renew':False,'account_id':'other'}):
            with patch.object(client,'request',side_effect=[{'email':'mother@example.test'},data]):
                with self.assertRaises(BusinessError):client.renewal('mother@example.test')
        with patch.object(client,'request') as req:
            with self.assertRaises(BusinessError):client.renewal('other@example.test')
            self.assertEqual(req.call_count,0)
        with patch.object(client,'request',return_value={'email':'other@example.test'}) as req:
            with self.assertRaises(BusinessError):client.renewal('mother@example.test')
            self.assertEqual(req.call_count,1)

    def test_transport_never_retries_cancel_post(self):
        client=TeamClient(token(),'team-fixture','http://fixture.invalid:8080')
        self.addCleanup(client.close)
        with patch.object(client.session,'request',side_effect=TimeoutError) as req:
            with self.assertRaises(UpstreamError):client.request('POST','/subscriptions/cancel',{'account_id':'team-fixture'})
            self.assertEqual(req.call_count,1)
            self.assertEqual(req.call_args.args[1],'https://chatgpt.com/backend-api/subscriptions/cancel')
            self.assertFalse(req.call_args.kwargs['allow_redirects'])


if __name__=='__main__':unittest.main()
