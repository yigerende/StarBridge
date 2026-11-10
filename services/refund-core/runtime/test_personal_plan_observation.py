"""One login, personal observation, same-session team switch: loopback only."""
import json
import base64
import unittest
from unittest.mock import Mock, patch

import diagnostics
import personal_plan_observation as observation
import refund_runner as runner
from test_refund_transport import ProxyFlow, job
from test_merge_v2 import session


class PersonalPlanObservationTests(unittest.TestCase):
    def run_flow(self, fixture_type=ProxyFlow, configure=None, recovery=False):
        value, evidence, sessions = job(), [], []
        if recovery:
            value["settings"]["recovery"] = {"enabled": True}
        def factory(*args):
            flow = runner.CustomerLogin(*args)
            sessions.append(flow.web_session)
            return flow
        with fixture_type(value) as fixture:
            if configure:
                configure(fixture)
            result = runner.execute(value, lambda name, *args: evidence.append((name, args)), login_factory=factory, sleep=lambda _: None)
        self.assertEqual(fixture.errors, [])
        return result, fixture, evidence, sessions

    def test_untyped_workspace_ids_query_plan_before_accepting_team(self):
        class Untyped(ProxyFlow):
            def reply(self, role, method, url, headers, raw):
                if url.path == "/workspace":
                    cookie = base64.urlsafe_b64encode(json.dumps({"workspaces": [
                        {"id": self.job["mother"]["team_id"]}, {"id": "fixture-personal"}
                    ]}).encode()).decode().rstrip("=")
                    return 200, {}, {"Set-Cookie": "oai-client-auth-session=" + cookie + "; Path=/"}
                if url.path.startswith("/backend-api/accounts/check/"):
                    assert not self.members, "personal observation must precede joining our team"
                return super().reply(role, method, url, headers, raw)
        result, fixture, events, sessions = self.run_flow(Untyped)
        self.assertTrue(result["ok"])
        self.assertEqual(len(sessions), 1)
        paths = [path for _, _, path, _, _ in fixture.records]
        self.assertEqual(paths.count("/api/accounts/password/verify"), 1)
        self.assertEqual(paths.count("/api/accounts/mfa/verify"), 1)
        plans = [args[0]["diagnostic"]["evidence"].get("workspaces", {})
                 for name, args in events if name == "diagnostic"]
        self.assertTrue(any(p.get("source") == "personal_accounts_check" and p.get("result") == "observed" for p in plans))

    def test_untyped_business_is_never_reported_as_personal(self):
        class Business(ProxyFlow):
            def reply(self, role, method, url, headers, raw):
                status, body, response_headers = super().reply(role, method, url, headers, raw)
                if url.path == "/workspace":
                    cookie = base64.urlsafe_b64encode(json.dumps({"workspaces": [
                        {"id": self.job["mother"]["team_id"]}, {"id": "fixture-personal"}
                    ]}).encode()).decode().rstrip("=")
                    response_headers["Set-Cookie"] = "oai-client-auth-session=" + cookie + "; Path=/"
                if url.path.startswith("/backend-api/accounts/check/"):
                    body["accounts"]["personal"]["account"]["structure"] = "workspace"
                return status, body, response_headers
        result, _, events, _ = self.run_flow(Business)
        self.assertTrue(result["ok"])
        plans = [args[0]["diagnostic"]["evidence"].get("workspaces", {})
                 for name, args in events if name == "diagnostic"]
        personal = [p for p in plans if p.get("source") == "personal_accounts_check"]
        self.assertEqual(len(personal), 1)
        self.assertEqual(personal[0]["result"], "not_personal")
        self.assertFalse(personal[0].get("items"))

    def test_multiple_untyped_workspaces_are_not_guessed(self):
        flow = Mock(target_team="our-team")
        flow.decode_oauth_session_cookie.return_value = {"workspaces": [
            {"id": "our-team"}, {"id": "other-a"}, {"id": "other-b"}]}
        with self.assertRaises(observation.PersonalWorkspaceUnavailable):
            observation.select_personal_workspace(flow, "https://auth.openai.com/consent")
        flow.request.assert_not_called()

    def test_one_password_and_totp_personal_plan_then_target_team_same_session(self):
        result, fixture, evidence, sessions = self.run_flow()
        self.assertTrue(result["ok"])
        self.assertEqual(len(sessions), 1)
        self.assertEqual(len(fixture.child_sessions), 1)
        paths = [path for role, method, path, headers, body in fixture.records if role == "child"]
        for path in ("/api/accounts/authorize/continue", "/api/accounts/password/verify", "/api/accounts/mfa/verify"):
            self.assertEqual(paths.count(path), 1)
        selections = [(i, json.loads(body)["workspace_id"]) for i, (_, _, path, _, body) in enumerate(fixture.records) if path.endswith("workspace/select")]
        self.assertEqual([value for _, value in selections], ["fixture-personal", "fixture-team"])
        query = next(i for i, (_, _, path, _, _) in enumerate(fixture.records) if path.startswith("/backend-api/accounts/check/"))
        self.assertLess(selections[0][0], query)
        self.assertLess(query, selections[1][0])
        plans = [args[0]["diagnostic"]["evidence"]["workspaces"] for name, args in evidence if name == "diagnostic" and args[0]["diagnostic"]["evidence"].get("workspaces", {}).get("source") == "personal_accounts_check"]
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["items"][0]["fields"]["entitlement.subscription_plan"], "unknown_pro_50x")
        self.assertEqual(plans[0]["items"][0]["fields"]["entitlement.rate_limit_multiplier"], "50")
        self.assertEqual(len(plans[0]["items"]), 1)
        self.assertEqual(len(fixture.used_codes), 2)
        self.assertEqual(paths.count("/backend-api/accounts/export_and_deactivate"), 1)

    def test_query_failure_records_failure_and_continues_team_flow(self):
        result, fixture, evidence, _ = self.run_flow(configure=lambda f: setattr(f, "plan_status", 503))
        self.assertTrue(result["ok"])
        records = [args[0]["diagnostic"] for name, args in evidence if name == "diagnostic"]
        self.assertTrue(any(d.get("http_status") == 503 and d["evidence"].get("workspaces", {}).get("result") == "unavailable" for d in records))

    def test_no_personal_workspace_does_not_select_customer_business_or_relogin(self):
        class BusinessOnly(ProxyFlow):
            def reply(self, role, method, url, headers, raw):
                if url.path == "/workspace":
                    return 200, {"oai-client-auth-session":{"workspaces":[{"id":"other-business","type":"workspace"}]}}, {}
                return super().reply(role, method, url, headers, raw)
        result, fixture, _, _ = self.run_flow(BusinessOnly)
        self.assertTrue(result["ok"])
        selections = [json.loads(body)["workspace_id"] for _, _, path, _, body in fixture.records if path.endswith("workspace/select")]
        self.assertEqual(selections, ["fixture-team"])

    def test_team_switch_reauthentication_does_not_resubmit_password_or_export(self):
        class Reauthentication(ProxyFlow):
            def reply(self, role, method, url, headers, raw):
                if url.path == "/authorize-team":
                    return 302, {}, {"Location": "https://auth.openai.com/log-in/password"}
                return super().reply(role, method, url, headers, raw)
        result, fixture, _, _ = self.run_flow(Reauthentication, recovery=True)
        self.assertFalse(result["ok"])
        self.assertTrue(result["retryable"])
        self.assertEqual(result["code"], "team_session_expired")
        self.assertEqual(result["evidence"]["reason"], "team_session_expired")
        self.assertEqual(result["evidence"]["login_page"], "login_password")
        self.assertEqual(result["evidence"]["continue_kind"], "password")
        paths = [path for _, _, path, _, _ in fixture.records]
        self.assertEqual(paths.count("/api/accounts/password/verify"), 1)
        self.assertEqual(paths.count("/api/accounts/mfa/verify"), 1)
        self.assertNotIn("/backend-api/accounts/export_and_deactivate", paths)

    def test_wrong_team_session_never_uses_personal_at_to_submit(self):
        class StalePersonal(ProxyFlow):
            def reply(self, role, method, url, headers, raw):
                if url.path == "/api/auth/session":
                    return 200, {"accessToken":self.personal_token,"account":{"id":"fixture-personal"},"user":{"email":self.job["account"]["email"]}}, {}
                return super().reply(role, method, url, headers, raw)
        result, fixture, _, _ = self.run_flow(StalePersonal)
        self.assertFalse(result["ok"])
        self.assertFalse(any(path.endswith("export_and_deactivate") for _, _, path, _, _ in fixture.records))

    def test_personal_conflict_then_reauthentication_recovers_on_scheduled_attempt(self):
        class ConflictOnce(ProxyFlow):
            conflict = False
            def reply(self, role, method, url, headers, raw):
                if url.path.endswith("workspace/select") and json.loads(raw)["workspace_id"] == "fixture-personal" and not self.conflict:
                    self.conflict = True
                    return 409, {"error": {"code": "private-upstream-code"}}, {}
                if url.path == "/authorize-team" and self.authorization == 2:
                    return 302, {}, {"Location": "https://auth.openai.com/log-in?private=not-logged"}
                return super().reply(role, method, url, headers, raw)
        value, events = job(), []
        value["settings"]["recovery"] = {"enabled": True}
        with ConflictOnce(value) as fixture:
            first = runner.execute(value, lambda name, *args: events.append((name, args)), sleep=Mock(side_effect=AssertionError("scheduler owns the delay")))
            self.assertFalse(first["ok"])
            self.assertTrue(first["retryable"])
            self.assertEqual(first["code"], "team_session_expired")
            self.assertEqual(first["evidence"]["login_page"], "login")
            self.assertEqual(first["evidence"]["continue_kind"], "login")
            paths = [path for _, _, path, _, _ in fixture.records]
            self.assertEqual(paths.count("/api/accounts/password/verify"), 1)
            self.assertFalse(any(path.endswith("export_and_deactivate") or path.endswith("users/child-user") for path in paths))
            detail = [args[0]["diagnostic"] for name, args in events if name == "diagnostic"]
            self.assertTrue(any(d.get("http_status") == 409 and d["evidence"].get("reason") == "personal_session_conflict" for d in detail))
            self.assertNotIn("private-upstream-code", json.dumps(detail))
            self.assertNotIn("not-logged", json.dumps(detail))
            # Simulate only the scheduler's subsequent claim, without another invite.
            value.update(step="login", recovery_mode="retry")
            second = runner.execute(value, lambda name, *args: events.append((name, args)))
            self.assertTrue(second["ok"])
        self.assertEqual(fixture.errors, [])
        paths = [path for _, _, path, _, _ in fixture.records]
        self.assertEqual(paths.count("/api/accounts/password/verify"), 2)
        self.assertEqual(paths.count("/api/accounts/mfa/verify"), 2)
        self.assertEqual(paths.count("/backend-api/accounts/export_and_deactivate"), 1)
        self.assertEqual(sum(name == "inviting" for name, _ in events), 1)
        self.assertEqual(sum(name == "completed" for name, _ in events), 1)

    def test_personal_selection_conflict_does_not_stop_a_usable_team_session(self):
        class Conflict(ProxyFlow):
            def reply(self, role, method, url, headers, raw):
                if url.path.endswith("workspace/select") and json.loads(raw)["workspace_id"] == "fixture-personal":
                    return 409, {}, {}
                return super().reply(role, method, url, headers, raw)
        result, fixture, _, _ = self.run_flow(Conflict, recovery=True)
        self.assertTrue(result["ok"])
        self.assertEqual(sum(path.endswith("export_and_deactivate") for _, _, path, _, _ in fixture.records), 1)

    def test_explicit_team_verification_is_not_retried(self):
        for path, page, code in [
            ("/email-verification", "email_otp_verification", "extra_verification"),
            ("/mfa-challenge", "mfa_challenge", "extra_verification"),
            ("/auth-challenge", "auth_challenge", "login_failed"),
            ("/passkey", "passkey", "login_failed"),
            ("/phone-verification", "phone_verification", "login_failed"),
        ]:
            class Challenge(ProxyFlow):
                def reply(self, role, method, url, headers, raw):
                    if url.path == "/authorize-team":
                        return 302, {}, {"Location": "https://auth.openai.com" + path}
                    return super().reply(role, method, url, headers, raw)
            with self.subTest(path=path):
                result, fixture, _, _ = self.run_flow(Challenge, recovery=True)
                self.assertEqual(result["code"], code)
                self.assertFalse(result["retryable"])
                self.assertEqual(result["evidence"]["login_page"], page)
                self.assertFalse(any(p.endswith("export_and_deactivate") for _, _, p, _, _ in fixture.records))


    def test_direct_default_callback_is_not_consumed_before_target_selection(self):
        class DefaultCallback(ProxyFlow):
            def reply(self, role, method, url, headers, raw):
                if url.path == "/authorize-team":
                    return 302, {}, {"Location": "https://chatgpt.com/api/auth/callback/openai?code=wrong-default-code"}
                return super().reply(role, method, url, headers, raw)
        result, fixture, _, _ = self.run_flow(DefaultCallback)
        self.assertTrue(result["ok"])
        self.assertNotIn("wrong-default-code", fixture.used_codes)

    def test_plan_observation_never_contains_team_or_credentials_or_gates_unknown_plan(self):
        events = []
        payload = {"accounts":{"personal":{"account":{"account_id":"personal","structure":"personal","plan_type":"future_pro_50x","access_token":"private-token"},
                    "entitlement":{"subscription_plan":"future_pro_50x","email":"private@example.test"}},
                    "team":{"account":{"account_id":"team","structure":"workspace","plan_type":"private-team"}}}}
        with diagnostics.capture(lambda _, p: events.append(p)):
            observation.record("observed", data=payload, account_id="personal")
        self.assertNotIn("private", json.dumps(events))
        self.assertEqual(events[0]["diagnostic"]["evidence"]["workspaces"]["items"][0]["fields"]["account.plan_type"], "future_pro_50x")
        with diagnostics.capture(Mock(side_effect=RuntimeError("private-db-error"))):
            observation.record("observed", data=payload, account_id="personal")

    def test_mismatched_personal_identity_is_not_queried(self):
        flow = Mock(payload={"email":"child@example.com"}, target_team="target-team", device_id="fixture")
        flow.request.return_value = Mock(status=200, json=lambda: session("personal", email="other@example.com"))
        observation.query_personal_plan(flow)
        flow.web_session.get.assert_not_called()

    def test_unknown_authorization_host_is_rejected_before_following(self):
        flow = Mock(payload={"email":"child@example.com"}, device_id="fixture")
        flow.request.return_value = Mock(status=200, json=lambda:{"url":"https://private.invalid/steal"}, location=lambda:"")
        with self.assertRaises(RuntimeError):
            observation.team_authorization(flow)
        self.assertEqual(flow.request.call_count, 1)

    def test_changed_state_and_changed_user_both_prevent_submission(self):
        for mismatch in ("state", "user"):
            class Mismatch(ProxyFlow):
                def reply(self, role, method, url, headers, raw):
                    status, body, response_headers = super().reply(role, method, url, headers, raw)
                    if mismatch == "state" and url.path.endswith("workspace/select") and self.selected_space == self.job["mother"]["team_id"]:
                        body["continue_url"] = body["continue_url"].replace("state=team-state", "state=other-state")
                    if mismatch == "user" and url.path == "/api/auth/session" and self.selected_space == self.job["mother"]["team_id"]:
                        import base64
                        claims = {"email":self.job["account"]["email"], "https://api.openai.com/auth":{
                            "chatgpt_account_id":self.selected_space,"chatgpt_user_id":"other-user"}}
                        body["accessToken"] = "x." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".x"
                    return status, body, response_headers
            with self.subTest(mismatch=mismatch):
                result, fixture, _, _ = self.run_flow(Mismatch)
                self.assertFalse(result["ok"])
                self.assertFalse(any(path.endswith("export_and_deactivate") for _, _, path, _, _ in fixture.records))

    def test_network_failure_does_not_report_previous_me_200_as_plan_status(self):
        flow = Mock(payload={"email":"child@example.com"}, target_team="target-team", device_id="fixture")
        flow.request.return_value = Mock(status=200, json=lambda:session("personal"))
        flow.web_session.get.side_effect = [Mock(status_code=200,json=lambda:{"email":"child@example.com"}), TimeoutError()]
        events = []
        with diagnostics.capture(lambda _, p:events.append(p)):
            observation.query_personal_plan(flow)
        self.assertEqual(events[0]["diagnostic"]["http_status"], 0)
        self.assertEqual(events[0]["diagnostic"]["evidence"]["workspaces"]["result"], "unavailable")


if __name__ == "__main__":
    unittest.main()
