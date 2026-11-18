import copy
import json
import unittest
from unittest.mock import Mock, patch

import diagnostics
import password_login
import refund_runner as runner
from refund_plan_fallback import PersonalLogin
from manager_oauth import upstream, bridge
from manager_oauth.adapter import ProjectProtocolLogin
from test_refund_transport import ProxyFlow, job


OFFER = {"page": {"type": "email_otp_verification", "payload": {"email_verification_mode": "passwordless_login"}},
         "continue_url": "https://auth.openai.com/email-verification"}
CALLBACK = "https://chatgpt.com/api/auth/callback/openai?code=synthetic"


class PasswordLoginTests(unittest.TestCase):
    def flow(self, cls=runner.CustomerLogin):
        flow = cls.__new__(cls)
        flow.payload = {"email": "child@example.test", "_totp_secret": "JBSWY3DPEHPK3PXP"}
        flow.headers = Mock(return_value={})
        response = Mock(status=200)
        response.json.return_value = {"client_auth_session": {"email": "child@example.test", "email_verification_mode": "passwordless_login"}}
        flow.request = Mock(return_value=response)
        flow.device_id, flow.proxy_url = "fixture-device", "http://fixture.invalid"
        flow.last_status, flow.last_stage = 200, "identifier"
        flow.login_details = {"login_method": "not_started"}
        return flow

    def test_offer_password_totp_and_no_totp_share_existing_protocol(self):
        for cls in (runner.CustomerLogin, PersonalLogin):
            for mfa in (False, True):
                with self.subTest(cls=cls, mfa=mfa):
                    flow = self.flow(cls)
                    response = {"page": {"type": "mfa_challenge"}} if mfa else {"continue_url": CALLBACK}
                    events = []
                    with patch.object(ProjectProtocolLogin, "log"), patch.object(upstream, "generate_openai_sentinel_token", return_value="sentinel") as sentinel, \
                         patch.object(upstream.ChatGPTProtocolLogin, "submit_modern_password", return_value=response) as password, \
                         patch.object(upstream.ChatGPTProtocolLogin, "complete_totp_challenge", return_value=CALLBACK) as totp, \
                         diagnostics.capture(lambda _, p: events.append(p)):
                        self.assertEqual(flow.complete_modern_login(copy.deepcopy(OFFER), "fixture-password", 0), CALLBACK)
                    password.assert_called_once_with("fixture-password")
                    self.assertEqual(flow.request.call_count,1)
                    self.assertTrue(flow.request.call_args.args[0].endswith('/client_auth_session_dump'))
                    sentinel.assert_called_once_with("fixture-device", "password_verify", "http://fixture.invalid")
                    self.assertEqual(totp.call_count, int(mfa))
                    self.assertEqual(flow.login_details['login_method'], 'password_totp' if mfa else 'password')
                    self.assertEqual([e['diagnostic']['evidence']['event'] for e in events if e['diagnostic']['evidence']['stage']=='password_fallback'], ['start','response'])
                    self.assertNotIn('fixture-password', json.dumps(events))

    def test_disabled_missing_password_and_other_verification_modes_do_not_switch(self):
        for enabled, password, mode in ((False,'password','passwordless_login'), (True,'','passwordless_login'),
                                        (True,'password','login'),(True,'password','signup'),(True,'password','')):
            flow=self.flow();flow.payload['_email_password_fallback']=enabled
            step=copy.deepcopy(OFFER);step['page']['payload']['email_verification_mode']=mode
            with patch.object(ProjectProtocolLogin,'submit_modern_password') as submit, patch.object(upstream,'fetch_login_verification_code') as fetch:
                with self.assertRaises(password_login.NeedsVerification):flow.complete_modern_login(step,password,0)
                submit.assert_not_called();fetch.assert_not_called()
            flow.request.assert_not_called()

    def test_session_read_must_match_account_before_sending_password(self):
        cases = [{}, {'client_auth_session':{}},
                 {'client_auth_session':{'email':'other@example.test','email_verification_mode':'passwordless_login'}},
                 {'client_auth_session':{'email':'child@example.test','username':{'kind':'email','value':'other@example.test'},'email_verification_mode':'passwordless_login'}},
                 {'client_auth_session':{'email':'child@example.test','email_verification_mode':'signup'}}]
        for body in cases:
            flow=self.flow();flow.request.return_value.json.return_value=body
            with patch.object(ProjectProtocolLogin,'submit_modern_password') as submit:
                with self.assertRaises(upstream.LoginFlowError):flow.complete_modern_login(copy.deepcopy(OFFER),'password',0)
                submit.assert_not_called()

    def test_password_response_email_never_loops_or_fetches_otp(self):
        for cls in (runner.CustomerLogin, PersonalLogin):
            flow=self.flow(cls)
            with patch.object(ProjectProtocolLogin,'log'), patch.object(upstream,'generate_openai_sentinel_token'), \
                 patch.object(upstream.ChatGPTProtocolLogin,'submit_modern_password',return_value=copy.deepcopy(OFFER)) as submit, \
                 patch.object(upstream,'fetch_login_verification_code') as fetch:
                for _ in range(2):
                    with self.assertRaises(password_login.NeedsVerification) as caught:flow.complete_modern_login(copy.deepcopy(OFFER),'password',0)
                    self.assertEqual(caught.exception.code,'email_verification_after_password')
                submit.assert_called_once();fetch.assert_not_called()

    def test_mfa_followup_email_is_not_a_callback(self):
        flow=self.flow();flow._password_submitted=True
        with patch.object(ProjectProtocolLogin,'complete_totp_challenge',return_value='https://auth.openai.com/email-verification'):
            with self.assertRaises(password_login.NeedsVerification) as caught:flow.complete_totp_challenge({},'secret')
        self.assertEqual(caught.exception.code,'email_verification_after_password')

    def test_returning_to_password_page_stops(self):
        flow=self.flow()
        with patch.object(ProjectProtocolLogin,'log'),patch.object(upstream,'generate_openai_sentinel_token'),patch.object(upstream.ChatGPTProtocolLogin,'submit_modern_password',return_value={'page':{'type':'login_password'}}) as submit:
            with self.assertRaises(upstream.LoginFlowError) as caught:flow.complete_modern_login(copy.deepcopy(OFFER),'password',0)
        self.assertEqual(caught.exception.code,'login_step_unsupported');submit.assert_called_once()

    def test_phone_and_passkey_not_classified_as_passwordless(self):
        for page in ('phone_verification','passkey','auth_challenge'):
            flow=self.flow()
            with patch.object(ProjectProtocolLogin,'submit_modern_password') as submit:
                with self.assertRaises(upstream.LoginFlowError):flow.complete_modern_login({'page':{'type':page}},'password',0)
            submit.assert_not_called()

    def test_setting_passed_to_regular_retry_verify_and_personal_fallback(self):
        from test_refund_runner import RunnerTests
        import refund_plan_fallback
        for mode in ('', 'verify', 'fallback'):
            value=RunnerTests().job('login');value['recovery_mode']=mode
            value['settings']['resource_recovery']={'email_password_fallback':False}
            value['settings']['recovery']={'enabled':True}
            payloads=[]
            def factory(_,payload):
                payloads.append(payload);flow=Mock();flow.login.side_effect=RuntimeError('synthetic');return flow
            if mode=='fallback':refund_plan_fallback.execute(value,Mock(),Mock(),factory)
            else:runner.execute(value,Mock(),lambda _:Mock(),factory,Mock())
            self.assertTrue(payloads,mode)
            self.assertTrue(all(p['_email_password_fallback'] is False for p in payloads))


class PasswordOfferProxy(ProxyFlow):
    def reply(self, role, method, url, headers, raw):
        if url.path=='/api/accounts/authorize/continue':
            super().reply(role,method,url,headers,raw)
            return 200,copy.deepcopy(OFFER),{'Set-Cookie':'password_offer=current; Path=/'}
        if url.path=='/api/accounts/password/verify':
            assert 'password_offer=current' in headers.get('Cookie','')
            assert 'password_dump=read' in headers.get('Cookie','')
            assert 'login_session=fixture-login' in headers.get('Cookie','')
        if url.path=='/api/accounts/client_auth_session_dump':
            assert method=='GET' and 'password_offer=current' in headers.get('Cookie','')
            if getattr(self, 'dump_failure', 0):
                return self.dump_failure, {'error': {'code': 'server_error'}}, {}
            return 200,{'client_auth_session':{'email':self.job['account']['email'],'email_verification_mode':'passwordless_login'}},{'Set-Cookie':'password_dump=read; Path=/'}
        if url.path=='/api/accounts/mfa/verify' and getattr(self, 'mfa_failure', 0):
            return self.mfa_failure, {'error': {'code': 'invalid_auth_step'}}, {}
        return super().reply(role,method,url,headers,raw)


class PasswordLoginTransportTests(unittest.TestCase):
    def run_flow(self, responses=None, recovery=False, dump_failure=0, mfa_failure=0):
        value=job();value['settings'].update(query_personal_plan=False,resource_recovery={'post_login_wait_seconds':0},recovery={'enabled':recovery})
        checkpoints=[]
        with PasswordOfferProxy(value) as fixture:
            fixture.password_responses=responses or []
            fixture.dump_failure=dump_failure
            fixture.mfa_failure=mfa_failure
            result=runner.execute(value,lambda name,*args:checkpoints.append(name),sleep=lambda _:None)
        self.assertEqual(fixture.errors,[])
        return result,fixture,checkpoints

    def test_real_http_password_offer_totp_team_and_single_submission(self):
        result,fixture,points=self.run_flow()
        self.assertTrue(result['ok'])
        self.assertEqual(len(fixture.child_sessions),1)
        self.assertEqual(points.count('merge_v2_submitting'),1)
        self.assertEqual(points.count('completed'),1)
        self.assertEqual(sum(p.endswith('/password/verify') for _,_,p,_,_ in fixture.records),1)

    def test_fresh_login_retry_repeats_switch_without_reinviting(self):
        result,fixture,points=self.run_flow([(503,{'error':{'code':'temporarily_unavailable'}})])
        self.assertTrue(result['ok']);self.assertEqual(points.count('login'),2)
        self.assertEqual(points.count('inviting'),1);self.assertEqual(points.count('merge_v2_submitting'),1)

    def test_password_error_never_reaches_refund(self):
        result,fixture,points=self.run_flow([(401,{'error':{'code':'invalid_password'}})])
        self.assertIn('password_failed',points);self.assertNotIn('merge_v2_submitting',points)
        self.assertFalse(fixture.members or fixture.invited)

    def test_email_after_password_stops_with_specific_evidence(self):
        result,fixture,points=self.run_flow([(200,copy.deepcopy(OFFER))],True)
        self.assertFalse(result['ok']);self.assertEqual(result['code'],'extra_verification')
        self.assertEqual(result['evidence']['reason'],'email_verification_after_password')
        self.assertNotIn('merge_v2_submitting',points)

    def test_429_goes_to_existing_scheduler_without_proxy_fault(self):
        result,fixture,points=self.run_flow([(429,{'error':{'code':'rate_limit_exceeded'}})],True)
        self.assertFalse(result['ok']);self.assertTrue(result['retryable'])
        self.assertEqual(result['http_status'],429);self.assertEqual(result['network_code'],0)
        self.assertNotIn('merge_v2_submitting',points)

    def test_session_read_failure_does_not_submit_password_or_refund(self):
        result,fixture,points=self.run_flow(recovery=True,dump_failure=503)
        self.assertFalse(result['ok']);self.assertTrue(result['retryable'])
        self.assertEqual(result['http_status'],503)
        self.assertEqual(result['evidence']['stage'],'auth_session_read')
        self.assertFalse(any(p.endswith('/password/verify') for _,_,p,_,_ in fixture.records))
        self.assertNotIn('merge_v2_submitting',points)

    def test_mfa_conflict_after_password_switch_retains_existing_retry(self):
        result,fixture,points=self.run_flow(recovery=True,mfa_failure=409)
        self.assertFalse(result['ok']);self.assertTrue(result['retryable'])
        self.assertEqual(result['evidence']['reason'],'mfa_state_conflict')
        self.assertEqual(result['network_code'],0)
        self.assertNotIn('merge_v2_submitting',points)


if __name__=='__main__':unittest.main()
