import json
import unittest
from unittest.mock import Mock, patch

import refund_runner as runner
from manager_oauth import adapter, upstream
import test_refund_runner


class LoginTransientHTTPTests(unittest.TestCase):
    def flow(self):
        flow = runner.CustomerLogin('fixture', {
            'email': 'child@example.com', 'password': 'private-password',
            'proxy': 'http://child.invalid:8081', 'credential_mode': 'chatgpt_at',
            'merge_v2': {'team_id': 'team'},
        })
        flow.first_failed_request = {}
        self.addCleanup(flow.close)
        return flow

    def response(self, status, text=None):
        body = {'error': {'code': 'server_error', 'message': 'private-response'}}
        response = Mock(status=status, headers={'Retry-After': '120'},
                        text=text if text is not None else json.dumps(body),
                        url='https://auth.openai.com/api/accounts/mfa/verify')
        response.json.return_value = body
        return response

    def test_actual_login_responses_schedule_one_attempt_without_cleanup(self):
        for status in [408, 429, 500, 502, 503, 504]:
            for enabled in [True, False]:
                with self.subTest(status=status, recovery=enabled):
                    flow = self.flow()
                    response = self.response(status)
                    job = test_refund_runner.RunnerTests().job('invited')
                    job['settings']['recovery'] = {'enabled': enabled}
                    mother, sleep = Mock(), Mock()
                    events = []
                    def login():
                        # Fresh sessions for each configured in-process attempt.
                        flow.rate_limit_error = None
                        return flow.request(response.url, method='POST')
                    with patch.object(flow, 'login', side_effect=login), \
                         patch.object(flow, '_web_session_request', return_value=response) as request, \
                         patch.object(flow, 'submit_export') as submit, \
                         patch.object(adapter, 'response_shape', return_value={}), \
                         patch.object(adapter.bridge, 'emit'):
                        result = runner.execute(job, lambda name, *args: events.append(name),
                                                lambda _: mother, lambda *_: flow, sleep)
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['http_status'], status)
                    self.assertEqual(result['code'], 'login_failed')
                    self.assertEqual(result['evidence']['retry_after_seconds'], 120)
                    self.assertEqual(result['evidence']['stage'], 'mfa_verify')
                    self.assertEqual(request.call_count, 1 if enabled else 2)
                    if enabled:
                        self.assertTrue(result['retryable'])
                        sleep.assert_not_called()
                    else:
                        sleep.assert_called_once_with(120)
                    submit.assert_not_called()
                    mother.cleanup_unaccepted.assert_not_called()
                    self.assertNotIn('cleanup_started', events)
                    self.assertNotIn('private-response', json.dumps(result))

    def test_challenges_and_unrelated_requests_are_not_transient_login(self):
        for url, response in [
            ('https://auth.openai.com/api/accounts/mfa/verify', self.response(500, '<html>Just a moment cf-chl-</html>')),
            ('https://chatgpt.com/backend-api/accounts/team/deactivate', self.response(500)),
            ('https://unrelated.invalid/api/auth/session', self.response(500)),
            ('https://auth.openai.com/api/accounts/mfa/verify', self.response(403)),
        ]:
            with self.subTest(url=url, status=response.status):
                flow = self.flow()
                with patch.object(flow, '_web_session_request', return_value=response), \
                     patch.object(adapter, 'response_shape', return_value={}), \
                     patch.object(adapter.bridge, 'emit'):
                    self.assertIs(flow.request(url, method='POST'), response)

    def test_stale_500_does_not_make_generic_failure_retryable(self):
        flow = Mock(last_status=500, last_stage='api_accounts_mfa_verify')
        flow.login.side_effect = RuntimeError('unrelated local failure')
        job = test_refund_runner.RunnerTests().job('invited')
        job['settings']['recovery'] = {'enabled': True}
        result = runner.execute(job, lambda *_: None, lambda _: Mock(), lambda *_: flow)
        self.assertFalse(result['retryable'])

    def test_mother_preflight_keeps_retry_after_for_scheduler(self):
        with patch.object(runner.requests, 'Session'):
            mother = runner.MotherClient(test_refund_runner.RunnerTests().job())
        self.addCleanup(mother.session.close)
        mother.session.request.return_value = Mock(status_code=429, headers={'Retry-After': '90'})
        with self.assertRaises(runner.UpstreamFailure) as raised:
            mother.preflight()
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(raised.exception.code, 'mother_preflight_failed')
        self.assertEqual(raised.exception.retry_after_seconds, 90)


if __name__ == '__main__':
    unittest.main()
