"""Missing target-team session retries login, with no real upstream requests."""
import base64
import json
import time
import unittest
from unittest.mock import Mock, patch

import refund_runner as runner
from manager_oauth import adapter, bridge, upstream
from test_merge_v2 import session
import test_refund_runner


def with_claims(value, change):
    claims = upstream.jwt_payload(value['accessToken'])
    change(claims)
    token = 'x.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.x'
    return {**value, 'access_token': token, 'accessToken': token}


class TeamSessionRetryTests(unittest.TestCase):
    def test_five_second_wait_precedes_workspace_flow_in_both_modes(self):
        for skip_observation in (True, False):
            with self.subTest(skip_observation=skip_observation):
                flow, events = self.flow(), []
                flow.payload['_skip_personal_plan_observation'] = skip_observation
                path = ('workspace_observation.WorkspaceObservationMixin._finish_chatgpt_login'
                        if skip_observation else 'personal_plan_observation.finish_personal_then_team')
                with patch.object(runner.time, 'sleep', side_effect=lambda seconds: events.append(('wait', seconds))), \
                     patch(path, side_effect=lambda *args: events.append(('continue', args[-1])) or 'session'):
                    self.assertEqual(flow._finish_chatgpt_login('synthetic-callback'), 'session')
                self.assertEqual(events, [('wait', 5), ('continue', 'synthetic-callback')])

    def test_configured_wait_zero_and_invalid_values(self):
        for seconds, expected in ((0, None), (12, 12), (60, 60), (-1, 5), (61, 5), (None, 5)):
            with self.subTest(seconds=seconds):
                flow = self.flow()
                flow.payload.update(_skip_personal_plan_observation=True, _post_login_wait_seconds=seconds)
                with patch.object(runner.time, 'sleep') as sleeper, patch('workspace_observation.WorkspaceObservationMixin._finish_chatgpt_login'):
                    flow._finish_chatgpt_login('synthetic-callback')
                if expected is None:
                    sleeper.assert_not_called()
                else:
                    sleeper.assert_called_once_with(expected)

    def test_job_wait_is_passed_to_each_new_login(self):
        job = test_refund_runner.RunnerTests().job('invited')
        job['settings']['resource_recovery'] = {'post_login_wait_seconds': 13}
        job['settings']['recovery'] = {'enabled': False}
        payloads = []
        def factory(_, payload):
            payloads.append(dict(payload))
            flow = Mock()
            flow.last_stage, flow.last_status = 'api_auth_session', 200
            flow.login.side_effect = upstream.LoginFlowError('Target team session is not ready', code='team_session_not_ready', retryable=True)
            return flow
        runner.execute(job, Mock(), lambda _: Mock(), factory, Mock())
        self.assertEqual(len(payloads), job['settings']['max_attempts'])
        self.assertTrue(all(p['_post_login_wait_seconds'] == 13 for p in payloads))

    def flow(self):
        flow = runner.CustomerLogin('fixture', {
            'email': 'child@example.com', 'credential_mode': 'chatgpt_at',
            'proxy': 'http://child.invalid:8081', 'merge_v2': {'team_id': 'target-team'},
        })
        self.addCleanup(flow.close)
        flow.last_stage = 'api_auth_session'
        flow.last_status = 200
        return flow

    def test_same_child_without_target_team_is_retryable_and_never_submits(self):
        values = [session('personal'), session('other-team'),
                  {**session(), 'account': {'id': 'personal'}},
                  with_claims(session(), lambda c: c['https://api.openai.com/auth'].pop('chatgpt_account_id'))]
        for value in values:
            with self.subTest(value=value['account']):
                flow = self.flow()
                flow.team_session_at = time.monotonic()
                with patch.object(flow.web_session, 'request') as request, patch.object(bridge, 'call') as checkpoint:
                    with self.assertRaises(upstream.LoginFlowError) as raised:
                        flow.submit_export(value)
                self.assertEqual(raised.exception.code, 'team_session_not_ready')
                self.assertTrue(raised.exception.retryable)
                request.assert_not_called()
                checkpoint.assert_not_called()

    def test_wrong_missing_or_conflicting_identity_keeps_original_rejection(self):
        no_email = with_claims(session('personal'), lambda c: c.pop('email'))
        no_email['user'] = {'id': 'child-user'}
        no_id = with_claims(session('personal'), lambda c: c['https://api.openai.com/auth'].pop('chatgpt_user_id'))
        conflict = session('personal', 'someone@example.com')
        conflict['user']['email'] = 'child@example.com'
        values = [session('personal', 'someone@example.com'), conflict, no_email, no_id,
                  {**session('personal'), 'access_token': 'invalid', 'accessToken': 'invalid'},
                  with_claims(session('personal'), lambda c: c.update({'https://api.openai.com/profile': {'email': 'other@example.com'}}))]
        for index, value in enumerate(values):
            with self.subTest(index=index):
                with self.assertRaises(RuntimeError) as raised:
                    self.flow().validate_team(value)
                self.assertFalse(getattr(raised.exception, 'retryable', False))

    def test_valid_target_keeps_original_token_and_identity(self):
        value = session()
        self.assertEqual(self.flow().validate_team(value), (value['access_token'], 'child-user'))

    def test_callback_session_failure_uses_durable_retry_without_cleanup(self):
        flow = self.flow()
        flow.login = flow._chatgpt_web_session
        flow.submit_export = Mock()
        flow.close = Mock(wraps=flow.close)
        mother, sleeper = Mock(), Mock()
        job = test_refund_runner.RunnerTests().job('invited')
        job['settings']['recovery'] = {'enabled': True}
        with patch.object(adapter.ProjectProtocolLogin, '_chatgpt_web_session', return_value=session('personal')):
            result = runner.execute(job, lambda *_: None, lambda _: mother, lambda *_: flow, sleeper)
        self.assertFalse(result['ok'])
        self.assertTrue(result['retryable'])
        self.assertEqual(result['code'], 'login_failed')
        self.assertEqual(result['http_status'], 200)
        self.assertEqual(result['network_code'], 0)
        self.assertEqual(result['evidence']['reason'], 'team_session_not_ready')
        self.assertEqual(result['evidence']['stage'], 'session')
        self.assertNotIn('child@example.com', json.dumps(result))
        self.assertIsNone(flow.team_session)
        self.assertIsNone(flow.team_session_at)
        flow.close.assert_called_once()
        flow.submit_export.assert_not_called()
        mother.invite.assert_not_called()
        mother.cleanup_unaccepted.assert_not_called()
        sleeper.assert_not_called()

    def test_local_retry_uses_new_session_then_submits_once(self):
        flows, events = [], []
        mother, sleeper = Mock(), Mock()
        job = test_refund_runner.RunnerTests().job('invited')
        job['settings']['recovery'] = {'enabled': False}
        response = Mock(status_code=200)
        response.json.return_value = {'success': True}

        def factory(*_):
            flow = self.flow()
            flows.append(flow)
            flow.login = flow._chatgpt_web_session
            flow.web_session.request = Mock(return_value=response)
            flow.close = Mock(wraps=flow.close)
            return flow

        with patch.object(adapter.ProjectProtocolLogin, '_chatgpt_web_session', side_effect=[session('personal'), session()]), patch.object(bridge, 'emit'):
            result = runner.execute(job, lambda name, *_: events.append(name), lambda _: mother, factory, sleeper)
        self.assertTrue(result['ok'])
        self.assertEqual(len(flows), 2)
        self.assertIsNot(flows[0].web_session, flows[1].web_session)
        flows[0].web_session.request.assert_not_called()
        flows[1].web_session.request.assert_called_once()
        for flow in flows:
            flow.close.assert_called_once()
        sleeper.assert_called_once_with(job['settings']['retry_seconds'])
        mother.invite.assert_not_called()
        mother.cleanup_unaccepted.assert_not_called()
        self.assertEqual(events.count('merge_v2_submitting'), 1)
        self.assertEqual(events.count('merge_v2_accepted'), 1)

    def test_unknown_submission_does_not_start_login(self):
        login, mother = Mock(), Mock()
        job = test_refund_runner.RunnerTests().job('submitting')
        job['settings']['recovery'] = {'enabled': True}
        result = runner.execute(job, Mock(), mother, login, Mock())
        self.assertEqual(result['code'], 'export_uncertain')
        login.assert_not_called()
        mother.assert_not_called()


if __name__ == '__main__':
    unittest.main()
