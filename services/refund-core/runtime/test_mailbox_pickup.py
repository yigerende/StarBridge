import base64
import copy
import io
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import diagnostics
import mailbox_pickup as mailbox
import password_login
import refund_runner as runner
from refund_plan_fallback import PersonalLogin
from manager_oauth import bridge, upstream
from manager_oauth.adapter import ProjectProtocolLogin
import test_password_login as password_fixture
from test_password_login import PasswordOfferProxy, OFFER, CALLBACK
from test_refund_transport import job

URL = 'https://mail.example.test/api/get-code?uid=private-fixture'


class MailboxParserTests(unittest.TestCase):
    def test_real_http_direct_cookie_adapter_and_token_adapter(self):
        records = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self): self.reply()
            def do_POST(self): self.reply()
            def reply(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                records.append((self.command, self.path, dict(self.headers), body))
                if self.path.startswith('/c/'):
                    value = {}; cookie = True
                elif self.path == '/api/public/code':
                    value = {'messages': [{'body': 'Your code is 724683'}]}; cookie = False
                elif self.path.startswith('/api/email-token/'):
                    value = {'ok': True, 'account': 'test@example.test'}; cookie = False
                elif self.path == '/api/emails':
                    value = {'ok': True, 'emails': [{'body': 'Your code is 724683'}]}; cookie = False
                else:
                    value = {'data': {'code': '724683'}}; cookie = False
                raw = json.dumps(value).encode()
                self.send_response(200)
                if cookie: self.send_header('Set-Cookie', 'mail_session=fixture; Path=/')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def connection(host, port, timeout):
            self.assertEqual(host, 'mail.example.test')
            return socket.create_connection(server.server_address, timeout)
        try:
            with patch.object(mailbox, 'connect_public', side_effect=connection):
                for suffix in ('/get-code?uid=fixture', '/c/1?token=fixture', '/m#fixture'):
                    reader = mailbox.MailboxReader('test@example.test', 'http://mail.example.test' + suffix)
                    self.assertEqual(reader.next_code(0, set()), '724683')
            self.assertEqual(len(records), 5)
            self.assertEqual(records[2][2].get('Cookie'), 'mail_session=fixture')
            self.assertTrue(all('Authorization' not in r[2] for r in records))
            self.assertTrue(all('Cookie' not in r[2] for r in records[:2] + records[3:]))
            self.assertEqual(json.loads(records[4][3]), {'account': 'test@example.test'})
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_space_parsers_with_varied_responses_without_network(self):
        samples = [json.dumps({'code': 0, 'message': 'SUCCESS', 'data': {'code': '724683'}}),
                   '724683', '<style>.x{color:#202123}</style><p>Your code is 724683</p>',
                   '<iframe src="data:text/html;base64,' + base64.b64encode(b'<p>Your code is 724683</p>').decode() + '"></iframe>']
        for raw in samples:
            with self.subTest(raw=raw[:15]), patch.object(mailbox.MailboxReader, 'text', return_value=raw), patch.object(socket, 'getaddrinfo') as dns:
                reader = mailbox.MailboxReader('test@example.test', URL)
                self.assertEqual(reader.next_code(0, set()), '724683')
                dns.assert_not_called()

    def test_old_timestamp_and_previously_submitted_codes_are_excluded(self):
        reader = mailbox.MailboxReader('test@example.test', URL)
        reader.fetch = Mock(side_effect=[
            [{'received_at': '2020-01-01T00:00:00Z', 'codes': ['724683']}],
            [{'codes': ['123456']}], [{'codes': ['654321']}],
        ])
        with patch.object(mailbox.time, 'sleep'):
            self.assertEqual(reader.next_code(1700000000, {'123456'}), '654321')
        self.assertEqual(reader.fetch.call_count, 3)

    def test_private_urls_redirects_and_mixed_dns_are_rejected(self):
        for url in ('file:///etc/passwd', 'http://127.0.0.1/a', 'http://[::1]/', 'http://169.254.169.254/',
                    'http://localhost/a', 'http://db.internal/', 'http://user:pass@example.test/', 'https://a.test/\nheader'):
            with self.subTest(url=url), self.assertRaises(mailbox.UnsafeMailbox):
                mailbox.validate_url(url)
        with self.assertRaises(mailbox.UnsafeMailbox):
            mailbox.Redirects().redirect_request(None, None, 302, '', {}, 'http://10.0.0.1/')
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))
        with patch.object(socket, 'getaddrinfo', return_value=[public, private]), patch.object(socket, 'socket') as sock:
            with self.assertRaises(mailbox.UnsafeMailbox): mailbox.connect_public('example.test', 443, 1)
            sock.assert_not_called()
        with patch.object(socket, 'getaddrinfo', return_value=[public]) as dns, patch.object(socket, 'socket') as sock:
            mailbox.connect_public('example.test', 443, 1)
            sock.return_value.connect.assert_called_once_with(('93.184.216.34', 443))
            self.assertEqual(dns.call_count, 1)

    def test_response_limit_and_private_failure_do_not_escape_as_network_fault(self):
        with self.assertRaises(mailbox.MailboxError):
            mailbox.BoundedResponse(io.BytesIO(b'x' * (mailbox.MAX_BODY + 1))).read()
        flow = password_fixture.PasswordLoginTests().flow()
        flow.payload['_pickup_url'] = URL; flow._password_verified = True
        with patch.object(mailbox, 'MailboxReader', side_effect=mailbox.UnsafeMailbox('blocked')):
            with self.assertRaises(password_login.NeedsVerification) as caught: flow.prepare_email_code()
        detail = diagnostics.exception_evidence(caught.exception, 'email_verification')
        self.assertEqual(detail['network_code'], 0)
        self.assertEqual(detail['evidence']['reason'], 'email_code_address_blocked')
        self.assertNotIn('private-fixture', json.dumps(detail))

    def test_poll_budget_and_secret_free_errors(self):
        reader = mailbox.MailboxReader('test@example.test', URL)
        reader.fetch = Mock(side_effect=RuntimeError(URL))
        with patch.object(mailbox, 'WAIT_SECONDS', 1), patch.object(mailbox.time, 'sleep', side_effect=lambda _: setattr(reader, 'deadline', 0)):
            with self.assertRaises(mailbox.MailboxError) as caught: reader.next_code(0, set())
        self.assertNotIn(URL, str(caught.exception)); self.assertIsNone(caught.exception.__context__)


class OptionalMailboxLoginTests(unittest.TestCase):
    def flow(self, cls=runner.CustomerLogin):
        flow = password_fixture.PasswordLoginTests().flow(cls)
        flow.payload['_pickup_url'] = URL
        return flow

    def test_normal_and_password_failed_login_never_read_mailbox(self):
        for failure in (False, True):
            flow = self.flow()
            with patch.object(mailbox, 'MailboxReader') as reader, patch.object(ProjectProtocolLogin, 'log'), \
                 patch.object(upstream, 'generate_openai_sentinel_token'), \
                 patch.object(upstream.ChatGPTProtocolLogin, 'submit_modern_password',
                              side_effect=upstream.LoginFlowError('bad', code='invalid_password', status=401) if failure else None,
                              return_value={'continue_url': CALLBACK}):
                if failure:
                    with self.assertRaises(upstream.LoginFlowError): flow.complete_modern_login(copy.deepcopy(OFFER), 'password', 0)
                else: self.assertEqual(flow.complete_modern_login(copy.deepcopy(OFFER), 'password', 0), CALLBACK)
                reader.assert_not_called()

    def test_missing_link_disabled_switch_or_initial_email_never_reads_mailbox(self):
        for kind in ('missing', 'disabled', 'mandatory_initial'):
            flow=self.flow(); step=copy.deepcopy(OFFER)
            if kind=='missing': flow.payload.pop('_pickup_url')
            if kind=='disabled': flow.payload['_email_password_fallback']=False
            if kind=='mandatory_initial': step['page']['payload']['email_verification_mode']='login'
            with patch.object(mailbox,'MailboxReader') as reader, patch.object(ProjectProtocolLogin,'log'), patch.object(upstream,'generate_openai_sentinel_token'), \
                 patch.object(upstream.ChatGPTProtocolLogin,'submit_modern_password',return_value=copy.deepcopy(OFFER)):
                with self.assertRaises(password_login.NeedsVerification): flow.complete_modern_login(step,'password',0)
                reader.assert_not_called()

    def test_both_login_paths_fetch_only_after_successful_password(self):
        for cls in (runner.CustomerLogin, PersonalLogin):
            flow=self.flow(cls); events=[]
            def password(_): events.append('password'); return copy.deepcopy(OFFER)
            def code(*_): events.append('mailbox'); return '724683'
            with patch.object(ProjectProtocolLogin,'log'),patch.object(upstream,'generate_openai_sentinel_token'), \
                 patch.object(upstream.ChatGPTProtocolLogin,'submit_modern_password',side_effect=password), \
                 patch.object(mailbox.MailboxReader,'next_code',side_effect=code), \
                 patch.object(upstream.ChatGPTProtocolLogin,'submit_modern_code',return_value={'continue_url':CALLBACK}) as submit:
                self.assertEqual(flow.complete_modern_login(copy.deepcopy(OFFER),'password',0),CALLBACK)
            self.assertEqual(events,['password','mailbox']);submit.assert_called_once_with('724683')
            self.assertNotIn('manual_email_code',flow.payload)

    def test_mfa_then_email_and_email_then_mfa_continue_in_same_flow(self):
        flow=self.flow(); flow._password_submitted=True;flow._password_verified=True
        with patch.object(ProjectProtocolLogin,'log'),patch.object(ProjectProtocolLogin,'complete_totp_challenge',return_value='https://auth.openai.com/email-verification'), \
             patch.object(mailbox.MailboxReader,'next_code',return_value='724683'),patch.object(upstream.ChatGPTProtocolLogin,'submit_modern_code',return_value={'continue_url':CALLBACK}):
            self.assertEqual(flow.complete_totp_challenge({},'secret'),CALLBACK)
        for page in ('email_otp_verification','login_password'):
            with patch.object(upstream.ChatGPTProtocolLogin,'submit_modern_code',return_value={'page':{'type':page}}):
                with self.assertRaises(password_login.NeedsVerification):flow.submit_modern_code('724683')

    def test_url_reaches_regular_retry_verification_and_plan_fallback(self):
        from test_refund_runner import RunnerTests
        import refund_plan_fallback
        for mode in ('','verify','fallback'):
            value=RunnerTests().job('login');value['recovery_mode']=mode
            value['account']['email_code_url']=URL;value['settings']['recovery']={'enabled':True}
            payloads=[]
            def factory(_,payload):
                payloads.append(payload);flow=Mock();flow.login.side_effect=RuntimeError('fixture');return flow
            if mode=='fallback':refund_plan_fallback.execute(value,Mock(),Mock(),factory)
            else:runner.execute(value,Mock(),lambda _:Mock(),factory,Mock())
            self.assertTrue(payloads,mode);self.assertTrue(all(p['_pickup_url']==URL for p in payloads))


class MailboxOfferProxy(PasswordOfferProxy):
    def reply(self, role, method, url, headers, raw):
        if url.path=='/api/accounts/password/verify':
            super().reply(role,method,url,headers,raw)
            return 200,copy.deepcopy(OFFER),{'Set-Cookie':'mail_challenge=required; Path=/'}
        if url.path=='/api/accounts/email-otp/validate':
            assert 'mail_challenge=required' in headers.get('Cookie','')
            assert json.loads(raw)['code']=='724683', 'email code differs'
            return 200,{'page':{'type':'mfa_challenge','payload':{'factors':[{'id':'factor','factor_type':'totp'}]}}},{}
        return super().reply(role,method,url,headers,raw)


class OptionalMailboxTransportTests(unittest.TestCase):
    def test_real_protocol_mailbox_totp_team_submit_once_and_cleanup(self):
        value=job();value['account']['email_code_url']=URL
        value['settings'].update(query_personal_plan=False,resource_recovery={'post_login_wait_seconds':0},recovery={'enabled':True})
        points=[]
        with MailboxOfferProxy(value) as fixture, patch.object(mailbox.MailboxReader,'next_code',return_value='724683') as pickup:
            result=runner.execute(value,lambda name,*args:points.append(name))
        self.assertEqual(fixture.errors,[],str([(r[1],r[2]) for r in fixture.records])+str(result));self.assertTrue(result['ok'],result)
        self.assertEqual(pickup.call_count,1);self.assertEqual(points.count('merge_v2_submitting'),1)
        self.assertEqual(points.count('completed'),1);self.assertEqual(len(fixture.child_sessions),1)
        self.assertFalse(fixture.members or fixture.invited)


if __name__=='__main__':unittest.main()
