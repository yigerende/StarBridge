import json
import unittest
from unittest.mock import Mock, patch

import refund_runner as runner
from manager_oauth import adapter, upstream
import test_refund_runner


class PasswordFailureTests(unittest.TestCase):
    def test_exact_password_response_only(self):
        flow = runner.CustomerLogin('fixture', {
            'email': 'child@example.com', 'password': 'private',
            'proxy': 'http://child.invalid:8081', 'credential_mode': 'chatgpt_at', 'merge_v2': {'team_id': 'team'},
        })
        self.addCleanup(flow.close)
        password_url = 'https://auth.openai.com/api/accounts/password/verify'
        for url, method, status, error, expected in [
            (password_url, 'POST', 401, {}, 'login_auth_failed'),
            (password_url, 'POST', 401, {'code': 'invalid_session'}, 'login_auth_failed'),
            (password_url, 'POST', 401, {'code': 'invalid_grant'}, 'login_auth_failed'),
            (password_url, 'POST', 401, {'code': []}, 'login_auth_failed'),
            (password_url, 'POST', 401, {'message': 'incorrect password'}, 'login_auth_failed'),
            (password_url, 'POST', 401, {'code': 'incorrect_password'}, 'incorrect_password'),
            (password_url, 'POST', 401, {'code': 'invalid_password'}, 'invalid_password'),
            (password_url, 'POST', 403, {'code': 'invalid_password'}, None),
            ('https://auth.openai.com/api/accounts/mfa/verify', 'POST', 401, {'code': 'invalid_password'}, None),
            (password_url, 'GET', 401, {'code': 'invalid_password'}, None),
        ]:
            response = Mock(status=status, text=json.dumps({'error': error}), headers={}, url=url)
            response.json.return_value = {'error': error}
            with self.subTest(url=url, method=method, status=status, error=error), patch.object(flow, '_web_session_request', return_value=response), patch.object(adapter, 'response_shape', return_value={}), patch.object(adapter.bridge, 'emit'):
                flow.first_failed_request = {}
                if expected:
                    with self.assertRaises(upstream.LoginFlowError) as raised:
                        flow.request(url, method=method)
                    self.assertEqual(raised.exception.code, expected)
                    self.assertEqual(raised.exception.retryable, expected == 'login_auth_failed')
                else:
                    self.assertIs(flow.request(url, method=method), response)

    def test_unclassified_401_uses_bounded_login_recovery_without_terminal_cleanup(self):
        for enabled in [False, True]:
            job = test_refund_runner.RunnerTests().job('invited')
            job['settings']['recovery'] = {'enabled': enabled}
            flows, events = [], []
            def login_factory(*args):
                flow = Mock(last_status=401, last_stage='api_accounts_password_verify')
                flow.login.side_effect = upstream.LoginFlowError('private', code='login_auth_failed', status=401, retryable=True)
                flows.append(flow)
                return flow
            mother, sleeper = Mock(), Mock()
            result = runner.execute(job, lambda name, *args: events.append(name), lambda _: mother, login_factory, sleeper)
            self.assertEqual(result['code'], 'login_auth_failed')
            self.assertEqual(result['http_status'], 401)
            self.assertEqual(len(flows), 1 if enabled else job['settings']['max_attempts'])
            self.assertNotIn('password_failed', events)
            mother.cleanup_unaccepted.assert_not_called()
            if enabled:
                self.assertTrue(result['retryable'])
                sleeper.assert_not_called()  # Durable scheduler handles the delay.
            else:
                sleeper.assert_called_once_with(job['settings']['retry_seconds'])
            for flow in flows:
                flow.close.assert_called_once()
                flow.submit_export.assert_not_called()

    def test_password_failure_cleans_immediately_never_relogs_or_submits(self):
        for enabled in [False, True]:
            job = test_refund_runner.RunnerTests().job('invited')
            job['settings']['recovery'] = {'enabled': enabled}
            flow = Mock()
            flow.login.side_effect = upstream.LoginFlowError('private response', code='incorrect_password', status=401)
            events = []
            mother, sleeper = Mock(), Mock()
            result = runner.execute(job, lambda name, *args: events.append(name), lambda _: mother, lambda *_: flow, sleeper)
            self.assertTrue(result['ok'])
            self.assertEqual([e for e in events if e != 'diagnostic'], ['login', 'password_failed', 'cleanup_started', 'cleanup_completed'])
            mother.cleanup_unaccepted.assert_called_once_with('child-user')
            flow.login.assert_called_once()
            flow.submit_export.assert_not_called()
            flow.close.assert_called_once()
            sleeper.assert_not_called()

    def test_checkpoint_or_cleanup_failure_never_finishes(self):
        for fail_checkpoint in [True, False]:
            flow = Mock()
            flow.login.side_effect = upstream.LoginFlowError('private', code='incorrect_password', status=401)
            mother = Mock()
            mother.cleanup_unaccepted.side_effect = runner.UpstreamFailure('mother_network', network_code=28)
            events = []
            def checkpoint(name, *args):
                events.append(name)
                if fail_checkpoint and name == 'password_failed':
                    raise runner.StopProtocol()
            result = runner.execute(test_refund_runner.RunnerTests().job('invited'), checkpoint, lambda _: mother, lambda *_: flow)
            self.assertFalse(result['ok'])
            self.assertNotIn('cleanup_completed', events)
            flow.submit_export.assert_not_called()
            if fail_checkpoint:
                mother.cleanup_unaccepted.assert_not_called()

    def test_other_stage_401_and_stale_status_never_cleanup(self):
        for exc in [RuntimeError('password verify failed HTTP 401'), upstream.LoginFlowError('MFA rejected', code='invalid_totp_code', status=401)]:
            flow = Mock(last_status=401, last_stage='api_accounts_mfa_verify')
            flow.login.side_effect = exc
            mother = Mock()
            result = runner.execute(test_refund_runner.RunnerTests().job('invited'), lambda *a: None, lambda _: mother, lambda *_: flow)
            self.assertEqual(result['code'], 'login_failed')
            mother.cleanup_unaccepted.assert_not_called()

    def test_invitation_revocation_requires_confirmed_absence(self):
        for response_lost in [False, True]:
            with patch.object(runner.requests, 'Session'):
                client = runner.MotherClient(test_refund_runner.RunnerTests().job())
            invite = {'id': 'invite-1', 'email_address': 'child@example.com', 'role': 'standard-user'}
            client.find = Mock(side_effect=[None, invite, None, None, None, None])
            client.call = Mock(side_effect=runner.UpstreamFailure('mother_network', network_code=28) if response_lost else None)
            client.cleanup_unaccepted('')
            client.call.assert_called_once_with('DELETE', '/invites', {'email_address': 'child@example.com'})
            self.assertEqual(client.find.call_count, 6)

    def test_invite_identity_guard_and_remaining_invite(self):
        for change in [{'email_address': 'other@example.com'}, {'role': 'account-owner'}, {'id': ''}, {}]:
            with patch.object(runner.requests, 'Session'):
                client = runner.MotherClient(test_refund_runner.RunnerTests().job())
            invite = {'id': 'invite-1', 'email_address': 'child@example.com', 'role': 'standard-user', **change}
            client.find = Mock(side_effect=[None, invite, None, invite])
            client.call = Mock()
            with self.assertRaises(runner.UpstreamFailure):
                client.cleanup_unaccepted('')
            if change:
                client.call.assert_not_called()

    def test_acceptance_race_removes_only_matching_member(self):
        with patch.object(runner.requests, 'Session'):
            client = runner.MotherClient(test_refund_runner.RunnerTests().job())
        invite = {'id': 'invite-1', 'email_address': 'child@example.com', 'role': 'standard-user'}
        member = {'id': 'new-child', 'role': 'standard-user'}
        client.find = Mock(side_effect=[None, invite, member, member, None, None, None])
        client.call = Mock()
        client.cleanup_unaccepted('')
        self.assertEqual([c.args for c in client.call.call_args_list], [('DELETE', '/invites', {'email_address': 'child@example.com'}), ('DELETE', '/users/new-child')])


if __name__ == '__main__':
    unittest.main()
