"""Only local proxy fixtures: no real accounts, proxy providers or upstream calls."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

import runner
from curl_cffi import requests
from test_refund_transport import ProxyFlow


def job():
    return {
        "id": "fixture", "step": "assigned", "child_user_id": "child-user",
        "account": {"email": "child@example.com", "password": "fixture", "totp_secret": "JBSWY3DPEHPK3PXP"},
        "mother": {"email": "mother@example.com", "team_id": "target-team", "access_token": "fixture", "seat_type": "default"},
        "mother_proxy": "http://mother.invalid:8080", "child_proxy": "http://child.invalid:8081",
        "settings": {"max_attempts": 3, "retry_seconds": 5, "recovery": {"enabled": True}},
    }


class ProxyBoundaryTests(unittest.TestCase):
    def run_transport(self, path, side="child", code=7, modify=None, ack_error=False):
        task = job()
        if modify:
            modify(task)
        steps, requests_failed = [], []
        with ProxyFlow(task) as fixture:
            fixture.members = task["step"] == "submitting" and task.get("recovery_mode") != "fallback"
            send = requests.Session.request

            def fail(session, method, url, **kwargs):
                if session.proxies.get("https") == task[side + "_proxy"] and urlsplit(url).path == path:
                    requests_failed.append((side, method))
                    raise requests.exceptions.ConnectionError(
                        "CONNECT tunnel failed, response 504" if code == 7 else "fixture timeout", code=code)
                return send(session, method, url, **kwargs)

            def checkpoint(method, params=None):
                steps.append((method, copy.deepcopy(params or {})))
                if ack_error and method == "child_proxy_network_failure":
                    raise runner.core.StopProtocol()
                return {}

            with patch.object(requests.Session, "request", fail):
                result = runner.execute(task, checkpoint=checkpoint)
            self.assertFalse(fixture.errors)
            records = [(side, method, url) for side, method, url, *_ in fixture.records]
        return result, steps, requests_failed, records

    def test_first_504_stops_original_same_proxy_login_loop_before_401(self):
        result, steps, failed, records = self.run_transport("/api/auth/csrf")
        self.assertEqual(len(failed), 1)
        self.assertEqual(result["network_code"], 7)
        self.assertEqual(result["evidence"]["proxy_http_status"], 504)
        self.assertEqual(result["code"], "login_failed")
        self.assertTrue(result["retryable"])
        self.assertEqual([m for m, _ in steps].count("child_proxy_network_failure"), 1)
        self.assertFalse(any("password" in path or "export_and_deactivate" in path for _, _, path in records))

    def test_later_child_stages_stop_at_first_transport_failure(self):
        for path in ("/api/accounts/password/verify", "/api/auth/session", "/backend-api/accounts/check/v4-2023-04-27"):
            with self.subTest(path=path):
                result, steps, failed, _ = self.run_transport(path)
                self.assertEqual(len(failed), 1)
                self.assertFalse(result["ok"])
                self.assertEqual([m for m, _ in steps].count("child_proxy_network_failure"), 1)
                self.assertNotIn("merge_v2_submitting", [m for m, _ in steps])

    def test_export_timeout_preserves_unknown_submission_and_never_removes(self):
        result, steps, failed, records = self.run_transport("/backend-api/accounts/export_and_deactivate", code=28)
        self.assertEqual(len(failed), 1)
        self.assertEqual(result["code"], "export_uncertain")
        methods = [m for m, _ in steps]
        self.assertIn("merge_v2_submitting", methods)
        self.assertIn("child_proxy_network_failure", methods)
        self.assertNotIn("merge_v2_rejected", methods)
        self.assertFalse(any(method == "DELETE" for _, method, _ in records))

    def test_export_not_sent_is_persisted_before_stopping_internal_retries(self):
        for enabled in (True, False):
            with self.subTest(recovery=enabled):
                result, steps, failed, _ = self.run_transport(
                    "/backend-api/accounts/export_and_deactivate",
                    modify=lambda j: j["settings"]["recovery"].update(enabled=enabled))
                self.assertEqual(len(failed), 1)
                self.assertEqual(result["code"], "export_not_sent")
                self.assertTrue(result["retryable"])
                methods = [m for m, _ in steps]
                self.assertEqual(methods[-2:], ["child_proxy_network_failure", "merge_v2_rejected"])
                self.assertEqual(methods.count("login"), 1)

    def test_verification_login_cools_without_export(self):
        def verify(j):
            j.update(step="submitting", recovery_mode="verify", verification_only=True,
                     personal_baseline={"id": "fixture-personal", "state": "active"})
        result, steps, failed, records = self.run_transport("/api/auth/csrf", modify=verify)
        self.assertEqual(len(failed), 1)
        self.assertEqual(result["code"], "verification_unavailable")
        self.assertIn("child_proxy_network_failure", [m for m, _ in steps])
        self.assertFalse(any("export_and_deactivate" in path or method == "DELETE" for _, method, path in records))

    def test_fallback_login_and_plan_query_also_stop_at_first_network_fault(self):
        def fallback(j):
            j.update(step="submitting", recovery_mode="fallback", fallback_phase="removed",
                     personal_baseline={"id": "fixture-personal", "state": "active"})
        for path in ("/api/auth/csrf", "/backend-api/accounts/check/v4-2023-04-27"):
            with self.subTest(path=path):
                result, steps, failed, records = self.run_transport(path, modify=fallback)
                self.assertEqual(len(failed), 1)
                self.assertEqual(result["code"], "fallback_unavailable")
                self.assertIn("child_proxy_network_failure", [m for m, _ in steps])
                self.assertFalse(any("export_and_deactivate" in path for _, _, path in records))

    def test_mother_removal_keeps_original_error_and_does_not_cool_child(self):
        result, steps, failed, _ = self.run_transport("/backend-api/accounts/target-team/users/child-user", side="mother", code=28)
        self.assertEqual(len(failed), 1)
        self.assertEqual(result["code"], "mother_network")
        self.assertEqual(result["evidence"]["side"], "mother")
        self.assertIn("merge_v2_accepted", [m for m, _ in steps])
        self.assertNotIn("child_proxy_network_failure", [m for m, _ in steps])

    def test_lost_cooldown_ack_still_stops_without_followup_request(self):
        result, steps, failed, records = self.run_transport("/api/auth/csrf", ack_error=True)
        self.assertEqual(len(failed), 1)
        self.assertEqual(result["network_code"], 7)
        self.assertEqual([m for m, _ in steps][-1], "child_proxy_network_failure")
        self.assertFalse(any("export_and_deactivate" in path for _, _, path in records))

    def test_password_rejection_does_not_request_proxy_switch(self):
        task = job()
        methods = []
        with ProxyFlow(task, password_failure=True) as fixture:
            result = runner.execute(task, checkpoint=lambda method, *args: methods.append(method))
            self.assertFalse(fixture.errors)
        self.assertNotIn("child_proxy_network_failure", methods)
        self.assertIn("password_failed", methods)
        self.assertTrue(result["ok"])  # original scoped cleanup completed

    def test_telemetry_budget_cannot_hide_network_failure_and_hook_is_restored(self):
        original = runner.diagnostics.exception_evidence
        task = job()
        send = Mock()
        def attempt(*args, **kwargs):
            with runner.diagnostics.capture(lambda *a: None):
                runner.diagnostics._count = 128
                runner.diagnostics.failure(TimeoutError(), "fallback_plan")
            self.fail("network failure was swallowed")
        task["recovery_mode"] = "fallback"
        with patch.object(runner.core, "execute", attempt):
            result = runner.execute(task, checkpoint=send)
        self.assertEqual(result["code"], "fallback_unavailable")
        self.assertEqual(send.call_args.args[0], "child_proxy_network_failure")
        self.assertIs(runner.diagnostics.exception_evidence, original)

    def test_auth_challenge_rate_limit_and_mother_are_not_child_network_faults(self):
        for evidence, status in [
            ({"side": "child", "reason": "incorrect_password"}, 401),
            ({"side": "child", "reason": "passkey_or_challenge"}, 403),
            ({"side": "child", "reason": "chatgpt_rate_limited"}, 429),
            ({"side": "child", "reason": "unknown_error"}, 503),
            ({"side": "mother", "reason": "network_error", "proxy_http_status": 504}, 0),
        ]:
            self.assertFalse(runner.child_network_failure({"http_status": status, "evidence": evidence}))

    def test_real_subprocess_ack_protocol_reports_one_network_failure(self):
        connections = []
        class UnavailableProxy(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_CONNECT(self):
                connections.append(self.command)
                self.send_error(504, "fixture unavailable")
            do_GET = do_CONNECT
            do_POST = do_CONNECT
        server = ThreadingHTTPServer(("127.0.0.1", 0), UnavailableProxy)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        process = None
        messages = []
        try:
            task = job()
            task["step"] = "login"  # no mother network request is needed
            task["child_proxy"] = f"http://127.0.0.1:{server.server_port}"
            process = subprocess.Popen([sys.executable, "-u", str(Path(runner.__file__))],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"},
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
            # Bound any regression in the subprocess/ACK handshake.
            timeout = threading.Timer(15, process.kill)
            timeout.start()
            try:
                process.stdin.write(json.dumps(task) + "\n")
                process.stdin.flush()
                for line in process.stdout:
                    message = json.loads(line)
                    messages.append(message)
                    if message["type"] == "checkpoint":
                        process.stdin.write('{"ok":true}\n')
                        process.stdin.flush()
                self.assertEqual(process.wait(timeout=2), 0)
            finally:
                timeout.cancel()
            signals = [m for m in messages if m.get("method") == "child_proxy_network_failure"]
            self.assertEqual(len(signals), 1)
            self.assertEqual(signals[0]["diagnostic"]["evidence"]["proxy_http_status"], 504)
            self.assertEqual(messages[-1]["type"], "result")
            self.assertEqual(messages[-1]["code"], "login_failed")
            self.assertFalse(messages[-1]["ok"])
            self.assertEqual(len(connections), 1)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdin.close()
                process.stdout.close()
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
