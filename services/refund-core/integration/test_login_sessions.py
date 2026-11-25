"""Synthetic login/submit failures only; no remote traffic or real credentials."""
import copy
import unittest
from unittest.mock import Mock

import runner
from test_runner import job, ProxyFlow


class LoginSessionTests(unittest.TestCase):
    def test_actual_login_authorize_403_returns_session_retry_without_submit(self):
        task = self.task()
        events = []
        with ProxyFlow(task) as fixture:
            original_reply = fixture.reply
            def reply(role, method, url, headers, raw):
                if role == "child" and url.path == "/authorize":
                    return 403, {"error": {"code": "proxy_exit_unavailable"}}, {}
                return original_reply(role, method, url, headers, raw)
            fixture.reply = reply
            result = runner.execute(task, checkpoint=lambda method, *args: events.append(method))
            self.assertFalse(fixture.errors)
            self.assertFalse(any("export_and_deactivate" in path for _, _, path, *_ in fixture.records))
        self.assertEqual(result["code"], "login_proxy_session_failed")
        self.assertEqual(result["evidence"]["stage"], "authorize")
        self.assertEqual(events.count("login"), 1)
        self.assertNotIn("child_proxy_network_failure", events)

    def task(self, recovery=True):
        task = job()
        task.update(step="invited", child_proxy_vendor="iproyal", child_proxy_session_ref="12345678",
                    child_proxy="http://fixture:secret_country-ph_session-12345678_lifetime-1h@gateway.invalid:80")
        task["settings"].update(max_attempts=3, recovery={"enabled": recovery})
        return task

    def error(self, status=403, code="chatgpt_authorize_failed", retryable=True):
        return runner.core.upstream.LoginFlowError("fixture", code=code, status=status, retryable=retryable)

    def run_login(self, task, failures, *, markers=None, ack_error=False, export_error=None):
        attempts, events, flows = [], [], []
        sleep, mother = Mock(), Mock()

        def checkpoint(method, params=None):
            events.append((method, copy.deepcopy(params)))
            if method == "child_login_session_retry" and ack_error:
                raise runner.core.StopProtocol()
            return {}

        def factory(_, payload):
            index = len(attempts)
            attempts.append(payload["proxy"])
            flow = Mock(last_stage="oauth_authorize", last_status=403,
                        first_failed_request={"body_markers": markers or []}, _login_step_evidence=None)
            flow.login.side_effect = failures[index] if index < len(failures) else None
            flow.login.return_value = {"access_token": "fixture"}
            if export_error:
                flow.submit_export.side_effect = export_error
            else:
                def export(_):
                    for method in ("merge_v2_ready", "merge_v2_submitting", "merge_v2_accepted"):
                        runner.core.bridge.call(method, team_id="target-team", user_id="child-user")
                    return {"success": True}
                flow.submit_export.side_effect = export
            flows.append(flow)
            return flow

        result = runner.execute(task, checkpoint=checkpoint, login_factory=factory,
                                mother_factory=lambda _: mother, sleep=sleep)
        return result, attempts, events, flows, sleep, mother

    def test_scheduled_retry_returns_typed_login_failure_without_replaying(self):
        result, attempts, events, flows, sleep, mother = self.run_login(self.task(), [self.error()])
        self.assertEqual(result["code"], "login_proxy_session_failed")
        self.assertEqual(result["http_status"], 403)
        self.assertEqual(result["evidence"]["stage"], "authorize")
        self.assertTrue(result["retryable"])
        self.assertEqual(len(attempts), 1)
        self.assertNotIn("child_proxy_network_failure", [m for m, _ in events])
        flows[0].submit_export.assert_not_called()
        sleep.assert_not_called()
        mother.invite.assert_not_called()

    def test_local_retry_renews_before_login_and_success_reuses_that_session(self):
        result, attempts, events, flows, sleep, _ = self.run_login(self.task(False), [self.error(), self.error()])
        self.assertTrue(result["ok"])
        self.assertEqual(len(set(attempts)), 3)
        self.assertTrue(all("_country-ph_session-" in url and "_lifetime-1h@" in url for url in attempts))
        self.assertEqual(sleep.call_args_list, [((5,),), ((5,),)])
        renewals = [p for m, p in events if m == "child_login_session_retry"]
        self.assertEqual(len(renewals), 2)
        self.assertEqual(renewals[1]["child_proxy_previous_ref"], renewals[0]["child_proxy_next_ref"])
        self.assertNotIn("child_proxy_network_failure", [m for m, _ in events])
        for flow in flows[:-1]:
            flow.submit_export.assert_not_called()
        flows[-1].submit_export.assert_called_once()

    def test_local_retry_exhaustion_does_not_allocate_an_unused_session(self):
        result, attempts, events, _, sleep, _ = self.run_login(self.task(False), [self.error()] * 3)
        self.assertFalse(result["ok"])
        self.assertEqual(len(set(attempts)), 3)
        self.assertEqual([m for m, _ in events].count("child_login_session_retry"), 2)
        self.assertEqual(sleep.call_count, 2)

    def test_lost_renewal_ack_never_uses_old_or_unacknowledged_endpoint(self):
        result, attempts, events, flows, _, _ = self.run_login(self.task(False), [self.error()], ack_error=True)
        self.assertEqual(result["code"], "protocol_failed")
        self.assertEqual(len(attempts), 1)
        self.assertEqual([m for m, _ in events].count("child_login_session_retry"), 1)
        flows[0].submit_export.assert_not_called()

    def test_explicit_errors_challenges_and_non403_keep_existing_rules(self):
        for error, markers in [
            (self.error(401, "login_auth_failed"), None),
            (self.error(429, "chatgpt_rate_limited"), None),
            (self.error(403, "account_disabled", False), None),
            (self.error(403, "incorrect_password", False), None),
            (self.error(403, "passkey_or_challenge", False), None),
            (self.error(), ["cf-chl-"]),
        ]:
            with self.subTest(code=error.code, markers=markers):
                result, attempts, events, _, _, _ = self.run_login(self.task(), [error], markers=markers)
                self.assertNotEqual(result["code"], "login_proxy_session_failed")
                self.assertEqual(len(attempts), 1)
                self.assertNotIn("child_login_session_retry", [m for m, _ in events])

    def test_export_error_with_stale_login_stage_does_not_renew(self):
        result, attempts, events, _, _, _ = self.run_login(self.task(), [], export_error=self.error())
        self.assertNotEqual(result["code"], "login_proxy_session_failed")
        self.assertEqual(len(attempts), 1)
        self.assertNotIn("child_login_session_retry", [m for m, _ in events])

    def test_connect_403_during_login_does_not_globally_cool_provider(self):
        error = runner.core.requests.exceptions.ProxyError("CONNECT tunnel failed, response 403", code=56)
        result, attempts, events, _, _, _ = self.run_login(self.task(), [error])
        self.assertEqual(result["code"], "login_proxy_session_failed")
        self.assertEqual(result["evidence"]["proxy_http_status"], 403)
        self.assertNotIn("child_proxy_network_failure", [m for m, _ in events])


if __name__ == "__main__":
    unittest.main()
