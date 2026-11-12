"""MFA 409 uses the existing login retry path; all transports are synthetic."""
import json
import unittest
from unittest.mock import Mock, patch

import refund_runner as runner
from manager_oauth import adapter, bridge, upstream
import test_refund_runner


VERIFY = 'https://auth.openai.com/api/accounts/mfa/verify'


class MFAConflictTests(unittest.TestCase):
    def flow(self):
        flow = runner.CustomerLogin('fixture', {
            'email': 'child@example.com', 'password': 'private-password',
            'proxy': 'http://child.invalid:8081', 'credential_mode': 'chatgpt_at',
            'merge_v2': {'team_id': 'target-team'},
        })
        flow.first_failed_request = {}
        flow.headers = Mock(return_value={})
        self.addCleanup(flow.close)
        return flow

    @staticmethod
    def response(status=409, body=None, text=None):
        body = body if body is not None else {'error': {'code': 'unclassified', 'message': 'private-response'}}
        response = Mock(status=status, headers={}, text=json.dumps(body) if text is None else text, url=VERIFY)
        response.json.return_value = body
        return response

    def test_only_current_mfa_post_409_is_retryable(self):
        cases = [(VERIFY, 'POST', 409, True), (VERIFY, 'GET', 409, False),
                 (VERIFY, 'POST', 400, False), (VERIFY, 'POST', 401, False),
                 (VERIFY, 'POST', 403, False), (VERIFY, 'POST', 200, False),
                 ('https://auth.openai.com/api/accounts/mfa/issue_challenge', 'POST', 409, False),
                 ('https://chatgpt.com/backend-api/accounts/team/deactivate', 'POST', 409, False),
                 ('https://unrelated.invalid/api/accounts/mfa/verify', 'POST', 409, False)]
        for url, method, status, retry in cases:
            with self.subTest(url=url, method=method, status=status):
                flow = self.flow()
                response = self.response(status)
                with patch.object(flow, '_web_session_request', return_value=response) as request, \
                     patch.object(adapter, 'response_shape', return_value={}), patch.object(bridge, 'emit'):
                    if retry:
                        with self.assertRaises(upstream.LoginFlowError) as raised:
                            flow.request(url, method=method)
                        self.assertEqual(raised.exception.code, 'mfa_state_conflict')
                        self.assertEqual(raised.exception.status, 409)
                        self.assertTrue(raised.exception.retryable)
                        self.assertNotIn('private-response', str(raised.exception))
                    else:
                        self.assertIs(flow.request(url, method=method), response)
                request.assert_called_once()

    def test_challenge_and_deactivated_account_keep_existing_handling(self):
        for kind in ('challenge', 'deactivated'):
            with self.subTest(kind=kind):
                flow = self.flow()
                response = self.response(text='<html>Just a moment cf-chl-</html>') if kind == 'challenge' else self.response(body={'error': {'code': 'account_deactivated'}})
                with patch.object(flow, '_web_session_request', return_value=response), \
                     patch.object(adapter, 'response_shape', return_value={}), patch.object(bridge, 'emit'):
                    if kind == 'challenge':
                        self.assertIs(flow.request(VERIFY, method='POST'), response)
                    else:
                        with self.assertRaises(bridge.DeadAccountError):
                            flow.request(VERIFY, method='POST')

    def test_existing_scheduler_receives_retry_without_submission_or_cleanup(self):
        flow = self.flow()
        flow.login = Mock(side_effect=lambda: flow.submit_mfa_verify('factor', '123456'))
        flow.submit_export = Mock()
        flow.close = Mock(wraps=flow.close)
        mother, sleeper = Mock(), Mock()
        job = test_refund_runner.RunnerTests().job('invited')
        job['settings']['recovery'] = {'enabled': True}
        with patch.object(flow, '_web_session_request', return_value=self.response()) as request, \
             patch.object(adapter, 'response_shape', return_value={}), patch.object(bridge, 'emit'):
            result = runner.execute(job, lambda *_: None, lambda _: mother, lambda *_: flow, sleeper)
        self.assertFalse(result['ok'])
        self.assertTrue(result['retryable'])
        self.assertEqual(result['code'], 'login_failed')
        self.assertEqual(result['http_status'], 409)
        self.assertEqual(result['network_code'], 0)
        self.assertEqual(result['evidence']['reason'], 'mfa_state_conflict')
        self.assertEqual(result['evidence']['stage'], 'mfa_verify')
        self.assertNotIn('private-response', json.dumps(result))
        request.assert_called_once()
        flow.close.assert_called_once()
        flow.submit_export.assert_not_called()
        mother.invite.assert_not_called()
        mother.cleanup_unaccepted.assert_not_called()
        sleeper.assert_not_called()  # The existing durable scheduler owns the delay.

    def test_existing_local_retry_creates_fresh_session_challenge_and_code(self):
        flows, submissions, events = [], [], []
        mother, sleeper = Mock(), Mock()
        job = test_refund_runner.RunnerTests().job('invited')
        job['settings']['recovery'] = {'enabled': False}
        def factory(*_):
            index = len(flows)
            flow = self.flow()
            flows.append(flow)
            step = {'page': {'payload': {'factors': [{'factor_type': 'totp', 'id': 'factor-' + str(index)}]}}}
            def request(url, **kwargs):
                if url == VERIFY:
                    submissions.append(kwargs['json_data'])
                    return self.response(409 if index == 0 else 200, None if index == 0 else {'continue_url': 'https://chatgpt.com/api/auth/callback/openai'})
                return self.response(200, {})
            flow._web_session_request = Mock(side_effect=request)
            flow.login = Mock(side_effect=lambda: flow.complete_totp_challenge(step, job['account']['totp_secret']))
            def export(_):
                bridge.call('merge_v2_ready', team_id='target-team', user_id='child-user')
                bridge.call('merge_v2_submitting', team_id='target-team')
                bridge.call('merge_v2_accepted')
                return {'success': True}
            flow.submit_export = Mock(side_effect=export)
            flow.close = Mock(wraps=flow.close)
            return flow
        with patch('pyotp.TOTP') as totp, patch.object(adapter, 'response_shape', return_value={}), patch.object(bridge, 'emit'):
            totp.return_value.now.side_effect = ['111111', '222222']
            result = runner.execute(job, lambda name, *_: events.append(name), lambda _: mother, factory, sleeper)
        self.assertTrue(result['ok'])
        self.assertEqual(len(flows), 2)
        self.assertIsNot(flows[0], flows[1])
        self.assertEqual([(v['id'], v['code']) for v in submissions], [('factor-0', '111111'), ('factor-1', '222222')])
        sleeper.assert_called_once_with(job['settings']['retry_seconds'])
        mother.invite.assert_not_called()
        mother.cleanup_unaccepted.assert_not_called()
        flows[0].submit_export.assert_not_called()
        flows[1].submit_export.assert_called_once()
        for flow in flows:
            flow.close.assert_called_once()
        self.assertEqual(events.count('merge_v2_submitting'), 1)


if __name__ == '__main__':
    unittest.main()
