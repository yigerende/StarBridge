import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import psycopg2
from psycopg2 import sql
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import db, registration_sessions as sessions, registration_gateway as gateway
from backend.common import BusinessError, digest, response
from backend.registration_docs import document


def complete_session():
    return {'user': {'id': 'synthetic-user', 'email': 'account@example.test'},
            'accessToken': 'LATEST_AT', 'expires': '2099-01-01T00:00:00.000Z',
            'custom': {'preserve': ['all', 'fields'], 'text': '第一行\n第二行'}}


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_uses_saved_cookie_proxy_and_retains_every_field(self):
        calls, options = [], []
        def handle(request):
            calls.append(request)
            return httpx.Response(200, json=complete_session())
        def factory(**kwargs):
            options.append(dict(kwargs))
            kwargs.pop('proxy')
            return httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs)
        asset = {'email': 'account@example.test', 'sessionToken': 'cookie_fixture',
                 'registrationProxyUrl': 'http://fixture:secret@proxy.example.test:8000'}
        result = await sessions.fetch_session(asset, client_factory=factory)
        self.assertEqual(result, complete_session())
        self.assertEqual(len(calls), 1)
        self.assertEqual(str(calls[0].url), sessions.SESSION_URL)
        self.assertEqual(calls[0].headers['cookie'], '__Secure-next-auth.session-token=cookie_fixture')
        self.assertEqual(options[0]['proxy'], asset['registrationProxyUrl'])
        self.assertFalse(options[0]['trust_env'])
        self.assertFalse(options[0]['follow_redirects'])

    async def test_invalid_empty_foreign_redirect_and_oversize_responses(self):
        responses = [httpx.Response(302, headers={'location': 'https://wrong.example.test'}),
                     httpx.Response(200, json={}), httpx.Response(200, text='<html>blocked</html>'),
                     httpx.Response(200, json={**complete_session(), 'error': 'synthetic_error'}),
                     httpx.Response(200, json={**complete_session(), 'user': {'email': 'other@example.test'}}),
                     httpx.Response(200, json={**complete_session(), 'expires': '2000-01-01T00:00:00Z'}),
                     httpx.Response(200, content=b'x' * (sessions.MAX_SESSION_BYTES + 1))]
        for result in responses:
            def factory(**kwargs):
                kwargs.pop('proxy')
                return httpx.AsyncClient(transport=httpx.MockTransport(lambda _: result), **kwargs)
            with self.assertRaises(sessions.CaptureFailure):
                await sessions.fetch_session({'email': 'account@example.test', 'sessionToken': 'test'}, client_factory=factory)

    async def test_proxy_missing_and_cookie_injection_never_send_request(self):
        for asset in ({'email': 'account@example.test', 'sessionToken': 'bad; Cookie=injected'},
                      {'email': 'account@example.test', 'sessionToken': 'test', 'sourceProxy': {'endpoint': 'proxy:8000'}}):
            with self.assertRaises(sessions.CaptureFailure):
                await sessions.fetch_session(asset, client_factory=lambda **_: self.fail('Must not send'))

    def test_completion_gate_waits_for_successful_oauth_and_skips_customer_engine_events(self):
        now = datetime.now(timezone.utc)
        job = {'id': 'job', 'status': 'complete', 'completedAt': now.isoformat(), 'sessionAssetId': 'asset'}
        state = {'runId': 'run', 'taskKind': 'register_session', 'jobs': [job]}
        self.assertEqual(len(sessions.completed_assets(state, now - timedelta(seconds=1))), 1)
        state['runPlan'] = {'postRegistrationOAuth': True}
        self.assertEqual(sessions.completed_assets(state, now - timedelta(seconds=1)), [])
        job['cpaPath'] = 'synthetic.json'
        self.assertEqual(len(sessions.completed_assets(state, now - timedelta(seconds=1))), 1)
        for status in ('running', 'partial', 'failed', 'waiting'):
            job['status'] = status
            self.assertEqual(sessions.completed_assets(state, now - timedelta(seconds=1)), [])
        job['status'] = 'complete'
        state['customerExecution'] = True
        self.assertEqual(sessions.completed_assets(state, now - timedelta(seconds=1)), [])
        state.pop('customerExecution')
        self.assertEqual(sessions.completed_assets(state, now + timedelta(seconds=1)), [])


class SessionDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name = 'session_capture_' + secrets.token_hex(6)
        cls.admin = psycopg2.connect('postgresql://gptreg_test@127.0.0.1:18983/postgres', connect_timeout=5)
        cls.admin.autocommit = True
        with cls.admin.cursor() as q:
            q.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.addClassCleanup(cls.cleanup)
        cls.env = patch.dict(os.environ, {'DATABASE_URL': 'postgresql://gptreg_test@127.0.0.1:18983/' + cls.name,
            'MASTER_KEY': base64.b64encode(secrets.token_bytes(32)).decode(), 'XUFU_INTEGRATION': '1',
            'REGISTRATION_GATEWAY_SECRET': 's' * 40})
        cls.env.start()
        db.initialize()
        for name, role in [('owner', 'member'), ('other', 'member'), ('admin', 'admin')]:
            db.execute('INSERT INTO users(id,email,name,password_hash,invite_code,role) VALUES($1,$2,$1,$1,$1,$3)', name, name + '@example.test', role)
            db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 day')", digest(name + '-cookie'), name)
            db.execute('INSERT INTO api_keys(id,user_id,name,hash,prefix) VALUES($1,$1,$1,$2,$1)', name, digest(name + '_' * 30))
        cls.server = subprocess.Popen(['node', str(ROOT / 'tests/fixtures/registration_session_server.cjs')], cwd=ROOT,
            env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                line = pool.submit(cls.server.stdout.readline).result(timeout=25)
            except Exception:
                cls.server.terminate()
                raise
        if not line:
            raise RuntimeError(cls.server.stderr.read())
        os.environ['REGISTRATION_URL'] = 'http://127.0.0.1:' + str(json.loads(line)['port'])

    @classmethod
    def cleanup(cls):
        if getattr(cls, 'server', None):
            cls.server.terminate()
            cls.server.wait(timeout=10)
            cls.server.stdout.close()
            cls.server.stderr.close()
        db.close()
        if getattr(cls, 'env', None):
            cls.env.stop()
        with cls.admin.cursor() as q:
            q.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s', (cls.name,))
            q.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)))
        cls.admin.close()

    def setUp(self):
        db.execute('DELETE FROM registration_session_captures')
        db.execute("UPDATE platform_accounts SET created_at=now() WHERE id='account-one'")
        db.execute('DELETE FROM rate_buckets')
        @asynccontextmanager
        async def lifespan(_):
            yield
            await gateway.close_transport()
        app = FastAPI(lifespan=lifespan)
        app.include_router(gateway.router)
        @app.exception_handler(BusinessError)
        async def errors(_, error):
            return response({'error': error.message}, error.status)
        self.app = app

    def tearDown(self):
        asyncio.run(gateway.close_transport())

    def ready(self):
        sessions.discover_deliveries()
        job = sessions.claim()
        sessions.save_result(job, 'account@example.test', complete_session())
        return job

    def client(self, owner='owner', api=False):
        client = TestClient(self.app, client=('127.0.0.1', 41000))
        if api:
            client.headers['Authorization'] = 'Bearer ' + owner + '_' * 30
        else:
            client.cookies.set('xupay_session', owner + '-cookie')
        client.headers['X-Requested-With'] = 'gptreg'
        return client

    def test_sixty_four_discoveries_and_claims_only_capture_once_and_cipher_full_json(self):
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(lambda _: sessions.discover_deliveries(), range(64)))
        self.assertEqual(db.scalar('SELECT count(*) FROM registration_session_captures'), 1)
        with ThreadPoolExecutor(max_workers=16) as pool:
            claims = list(pool.map(lambda _: sessions.claim(), range(64)))
        claimed = [job for job in claims if job]
        self.assertEqual(len(claimed), 1)
        sessions.save_result(claimed[0], 'account@example.test', complete_session())
        stored = db.one('SELECT * FROM registration_session_captures')
        self.assertEqual(stored['state'], 'ready')
        self.assertNotIn('LATEST_AT', stored['session_cipher'])
        self.assertNotIn('account@example.test', stored['session_cipher'])
        self.assertEqual(sessions.attach_sessions([{'asset_id': 'asset-one', 'email': 'account@example.test'}])[0]['session'], complete_session())
        self.assertEqual(db.scalar('SELECT count(*) FROM platform_ledger'), 0)

    def test_network_retries_are_bounded_and_stale_lease_cannot_overwrite(self):
        sessions.discover_deliveries()
        job = sessions.claim()
        first = dict(job)
        for attempt in range(1, 4):
            self.assertEqual(job['attempts'], attempt)
            sessions.fail_result(job, sessions.CaptureFailure('network_error', True))
            if attempt < 3:
                db.execute('UPDATE registration_session_captures SET available_at=now()')
                job = sessions.claim()
        self.assertEqual(db.scalar('SELECT state FROM registration_session_captures'), 'failed')
        sessions.save_result(first, 'account@example.test', complete_session())
        self.assertIsNone(db.scalar('SELECT session_cipher FROM registration_session_captures'))
        self.assertIsNone(sessions.claim())

    def test_crashed_worker_lease_is_reclaimed_without_duplicate_delivery(self):
        sessions.discover_deliveries()
        first = sessions.claim()
        db.execute("UPDATE registration_session_captures SET lease_until=now()-interval '1 second'")
        second = sessions.claim()
        sessions.save_result(first, 'account@example.test', complete_session())
        self.assertEqual(db.scalar('SELECT state FROM registration_session_captures'), 'running')
        sessions.save_result(second, 'account@example.test', complete_session())
        self.assertEqual(db.scalar('SELECT state FROM registration_session_captures'), 'ready')
        self.assertEqual(db.scalar('SELECT count(*) FROM platform_accounts'), 1)

    def test_gateway_user_admin_and_api_get_full_json_without_upstream_fetch(self):
        self.ready()
        for owner, api, prefix in [('owner', False, '/api/user/v1'), ('admin', False, '/api/admin/v1'), ('owner', True, '/open/v1')]:
            with self.client(owner, api) as client, patch.object(sessions, 'fetch_session', side_effect=AssertionError('Reading must not refresh')):
                r = client.get('/registration' + prefix + '/accounts/account-one/credentials')
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()['session'], complete_session())
                self.assertNotIn('session_token', r.json())
                self.assertEqual(r.headers['cache-control'], 'no-store')
                r = client.post('/registration' + prefix + '/accounts/export', json={'ids': ['account-one'], 'format': 'email_session'})
                self.assertEqual(r.status_code, 200, r.text)
                lines = r.json()['content'].splitlines()
                self.assertEqual(len(lines), 1)
                email, raw = lines[0].split('----', 1)
                self.assertEqual(email, 'account@example.test')
                self.assertEqual(json.loads(raw), complete_session())

    def test_gateway_blocks_other_owner_expired_feature_disabled_and_nonadmin(self):
        self.ready()
        with self.client('other') as client:
            self.assertEqual(client.get('/registration/api/user/v1/accounts/account-one/credentials').status_code, 404)
            self.assertEqual(client.post('/registration/api/user/v1/accounts/export', json={'ids': ['account-one'], 'format': 'email_session'}).status_code, 404)
            self.assertEqual(client.get('/registration/api/admin/v1/accounts/account-one/credentials').status_code, 403)
        db.execute("UPDATE platform_accounts SET created_at=now()-interval '73 hours'")
        with self.client() as client:
            self.assertEqual(client.get('/registration/api/user/v1/accounts/account-one/credentials').status_code, 404)
        with self.client('admin') as client:
            self.assertEqual(client.get('/registration/api/admin/v1/accounts/account-one/credentials').status_code, 200)
        db.execute("UPDATE platform_accounts SET created_at=now()")
        db.execute("INSERT INTO settings(key,value) VALUES('features',$1) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", {'accounts': False})
        try:
            with self.client() as client:
                self.assertEqual(client.post('/registration/api/user/v1/accounts/export', json={'ids': ['account-one'], 'format': 'email_session'}).status_code, 403)
        finally:
            db.execute("DELETE FROM settings WHERE key='features'")

    def test_missing_session_never_falls_back_to_cookie_and_existing_formats_still_work(self):
        with self.client() as client:
            path = '/registration/api/user/v1/accounts'
            detail = client.get(path + '/account-one/credentials')
            self.assertEqual(detail.json()['session_status'], 'pending')
            self.assertIsNone(detail.json()['session'])
            self.assertEqual(client.post(path + '/export', json={'ids': ['account-one'], 'format': 'email_session'}).status_code, 409)
            at = client.post(path + '/export', json={'ids': ['account-one'], 'format': 'email_at'})
            self.assertIn('ORIGINAL_AT', at.json()['content'])
            txt = client.post(path + '/export', json={'ids': ['account-one'], 'format': 'txt'})
            self.assertIn('fixture-password', txt.json()['content'])
            self.assertNotIn('ORIGINAL_COOKIE_TOKEN', detail.text)
        db.execute("UPDATE platform_accounts SET created_at=now()-interval '1 day'")
        sessions.discover_deliveries()
        self.assertEqual(db.scalar('SELECT count(*) FROM registration_session_captures'), 0)

    def test_capture_failure_does_not_change_successful_order_or_wallet(self):
        sessions.discover_deliveries()
        job = sessions.claim()
        with patch.object(sessions, 'engine_asset', new=AsyncMock(return_value={'id': 'asset-one', 'email': 'account@example.test', 'sessionToken': 'cookie'})), \
             patch.object(sessions, 'fetch_session', new=AsyncMock(side_effect=sessions.CaptureFailure('http_401'))):
            asyncio.run(sessions.capture(job))
        self.assertEqual(db.scalar('SELECT state FROM registration_session_captures'), 'failed')
        self.assertEqual(db.scalar('SELECT status FROM platform_orders'), 'completed')
        self.assertEqual(db.scalar('SELECT status FROM platform_items'), 'delivered')
        self.assertEqual(db.scalar('SELECT count(*) FROM platform_ledger'), 0)

    def test_checkpoint_observer_is_read_only_and_restart_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / '.runtime'
            directory = root / 'state/v2'
            directory.mkdir(parents=True)
            (root / 'gpt-re.config.json').write_text(json.dumps({'output': {'stateDir': '.runtime/state/v2'}}))
            file = directory / 'run-test.json'
            state = {'runId': 'run-test', 'taskKind': 'register_session',
                     'runPlan': {'postRegistrationOAuth': True},
                     'jobs': [{'id': 'job-one', 'sessionAssetId': 'raw-asset', 'status': 'complete',
                               'completedAt': datetime.now(timezone.utc).isoformat()}]}
            file.write_text(json.dumps(state))
            observer = sessions.RunObserver()
            with patch.dict(os.environ, {'REGISTRATION_RUNTIME_DIR': str(root)}):
                observer.discover()
                self.assertEqual(db.scalar('SELECT count(*) FROM registration_session_captures'), 0)
                state['jobs'][0]['cpaPath'] = 'synthetic.json'
                file.write_text(json.dumps(state))
                original = file.read_bytes()
                observer.discover()
                sessions.RunObserver().discover()
                self.assertEqual(file.read_bytes(), original)
                self.assertEqual(db.scalar('SELECT count(*) FROM registration_session_captures'), 1)

    def test_capture_queue_keeps_two_slots_busy_without_waiting_between_accounts(self):
        for number in range(12):
            sessions.enqueue('parallel:' + str(number), 'asset-' + str(number), datetime.now(timezone.utc))
        active, maximum = 0, 0
        async def fetch(_):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(.05)
                return complete_session()
            finally:
                active -= 1
        async def verify():
            with patch.object(sessions.RunObserver, 'discover'), patch.object(sessions, 'discover_deliveries'), \
                 patch.object(sessions, 'engine_asset', new=AsyncMock(return_value={'email': 'account@example.test'})), \
                 patch.object(sessions, 'fetch_session', new=fetch):
                task = asyncio.create_task(sessions.run())
                try:
                    async with asyncio.timeout(10):
                        while await asyncio.to_thread(db.scalar, "SELECT count(*) FROM registration_session_captures WHERE state='ready'") < 12:
                            await asyncio.sleep(.05)
                finally:
                    await sessions.stop(task)
        asyncio.run(verify())
        self.assertEqual(maximum, 2)
        self.assertEqual(active, 0)


class DocumentationTests(unittest.TestCase):
    def test_user_contract_examples_export_real_json(self):
        spec = document()
        fields = spec['components']['schemas']['RegistrationStoredCredentials']['properties']
        self.assertIn('session', fields)
        self.assertNotIn('session_token', fields)
        route = spec['paths']['/registration/open/v1/accounts/export']['post']
        examples = route['responses']['200']['content']['application/json']['examples']
        _, raw = examples['email_session']['value']['content'].strip().split('----', 1)
        self.assertIsInstance(json.loads(raw), dict)
        account = json.loads(examples['json']['value']['content'])[0]
        self.assertIsInstance(account['session'], dict)
        self.assertNotIn('session_token', account)


if __name__ == '__main__':
    unittest.main()
