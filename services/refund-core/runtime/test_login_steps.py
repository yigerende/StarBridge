import json
import unittest
from unittest.mock import Mock, patch

import diagnostics
import refund_runner as runner
from manager_oauth import upstream
from manager_oauth.adapter import ProjectProtocolLogin
import test_refund_runner


class LoginStepTests(unittest.TestCase):
    def flow(self):
        flow = runner.CustomerLogin.__new__(runner.CustomerLogin)
        flow.payload = {"_totp_secret": "JBSWY3DPEHPK3PXP"}
        flow.device_id = "fixture"
        flow.proxy_url = "http://proxy.invalid"
        flow.last_status = 200
        flow.last_stage = "api_accounts_authorize_continue"
        flow.login_details = {"login_method": "not_started"}
        return flow

    def test_missing_response_is_retryable_not_email_verification(self):
        for data in [{}, {"page": {}}, None, []]:
            with self.subTest(data=data):
                with self.assertRaises(upstream.LoginFlowError) as caught:
                    self.flow().complete_modern_login(data, "password", 0)
                self.assertTrue(caught.exception.retryable)
                self.assertEqual(caught.exception.code, "login_response_incomplete")

    def test_explicit_email_verification_stops_without_code_requests(self):
        for data in [{"page": {"type": "email_otp_verification"}},
                     {"continue_url": "/email-verification?token=private-secret"}]:
            flow = self.flow()
            with patch.object(upstream, "fetch_login_verification_code") as fetch:
                with self.assertRaises(runner.NeedsVerification):
                    flow.complete_modern_login(data, "password", 0)
                fetch.assert_not_called()
            self.assertEqual(diagnostics.exception_evidence(runner.NeedsVerification(), "email_verification", flow=flow)["evidence"]["reason"], "extra_verification")

    def test_security_challenge_is_not_retried(self):
        for page in ["auth_challenge", "passkey", "webauthn", "phone_verification", "unknown_challenge"]:
            with self.subTest(page=page), self.assertRaises(upstream.LoginFlowError) as caught:
                self.flow().complete_modern_login({"page": {"type": page}}, "password", 0)
            self.assertFalse(caught.exception.retryable)

    def test_password_then_totp_continues_normally(self):
        flow = self.flow()
        flow.complete_totp_challenge = Mock(return_value="callback-fixture")
        with patch.object(ProjectProtocolLogin, "log"), patch.object(upstream, "generate_openai_sentinel_token"), patch.object(ProjectProtocolLogin, "submit_modern_password", return_value={"page": {"type": "mfa_challenge"}}) as password:
            self.assertEqual(flow.complete_modern_login({"page": {"type": "login_password"}}, "password", 0), "callback-fixture")
        password.assert_called_once_with("password")
        flow.complete_totp_challenge.assert_called_once()

    def test_password_response_is_checked_too(self):
        for data, exception in [({}, upstream.LoginFlowError), ({"page": {"type": "email_otp_verification"}}, runner.NeedsVerification)]:
            with patch.object(ProjectProtocolLogin, "log"), patch.object(upstream, "generate_openai_sentinel_token"), patch.object(ProjectProtocolLogin, "submit_modern_password", return_value=data):
                with self.assertRaises(exception):
                    self.flow().complete_modern_login({"page": {"type": "login_password"}}, "password", 0)

    def test_callback_and_consent_are_preserved(self):
        with patch.object(ProjectProtocolLogin, "log"):
            flow = self.flow()
            for url in ["https://chatgpt.com/api/auth/callback/openai?code=private-secret", "/consent"]:
                self.assertEqual(flow.complete_modern_login({"continue_url": url}, "password", 0), flow.normalize_auth_url(url))

    def test_private_step_evidence_has_only_categories(self):
        rows = []
        with diagnostics.capture(lambda _, p: rows.append(p)):
            evidence = diagnostics.login_step({"page": {"type": "private-page", "payload": {"email_verification_mode": "private-mode"}}, "continue_url": "https://private.invalid/path?token=private-token"}, "identifier")
        self.assertEqual(evidence["login_page"], "other")
        self.assertEqual(evidence["continue_kind"], "other")
        self.assertNotIn("private", json.dumps(rows))

    def test_worker_sends_incomplete_response_to_durable_scheduler(self):
        job = test_refund_runner.RunnerTests().job("login")
        job["settings"]["recovery"] = {"enabled": True}
        flow = self.flow()
        flow.login = Mock(side_effect=lambda: flow.complete_modern_login({}, "password", 0))
        flow.close = Mock()
        flow.submit_export = Mock()
        sleeps = Mock()
        result = runner.execute(job, lambda *a: None, lambda _: Mock(), lambda *a: flow, sleeps)
        self.assertEqual(result["code"], "login_response_incomplete")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["evidence"]["login_page"], "missing")
        flow.login.assert_called_once()
        flow.close.assert_called_once()
        flow.submit_export.assert_not_called()
        sleeps.assert_not_called()
