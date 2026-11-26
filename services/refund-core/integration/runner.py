"""StarBridge's durable child-proxy boundary around the unchanged HUIXU runner.

Each worker attempt owns one subprocess. Transport evidence is intercepted before
the runtime's local retry/optional-observation handlers can discard it. Cooling
uses an acknowledged checkpoint; the asynchronous diagnostic queue stays passive.
"""
import json
from pathlib import Path
import runpy
import secrets
import sys

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import diagnostics
import refund_runner as core


NETWORK_EXCEPTIONS = {
    "ProxyError", "ConnectionError", "TimeoutError", "ConnectTimeout",
    "ReadTimeout", "ConnectionResetError", "DNSError", "SSLError",
}


def child_network_failure(detail):
    evidence = detail.get("evidence") or {}
    if evidence.get("side") != "child":
        return False
    if evidence.get("proxy_http_status") in {407, 502, 503, 504}:
        return True
    return (evidence.get("reason") == "network_error" and bool(detail.get("network_code"))) or (
        evidence.get("reason") in {"network_error", "unknown_error"}
        and evidence.get("exception") in NETWORK_EXCEPTIONS
    )


class EndChildAttempt(BaseException):
    """Must bypass the imported runtime's Exception-based local retry loops."""

    def __init__(self, diagnostic):
        self.diagnostic = diagnostic


class ChildProxyBoundary:
    def __init__(self, job, checkpoint):
        self.job = job
        self.send = checkpoint
        self.failure = None
        self.original_evidence = diagnostics.exception_evidence
        self.in_login = False
        self.login_failure = None
        self.login_error = None

    def login_factory(self, factory, job_id, payload):
        # The original runtime controls whether/when another local attempt exists.
        # Persist its new session before constructing that attempt's HTTP client.
        if self.login_failure is not None:
            vendor = self.job.get("child_proxy_vendor")
            previous = self.job.get("child_proxy_session_ref")
            prefix = {"iproyal": "_session-", "cliproxy": "-sid-",
                      "1024proxy": "-sid-", "711proxy": "-session-"}.get(vendor)
            if prefix and previous:
                next_ref = previous
                while next_ref == previous:
                    next_ref = f"{secrets.randbelow(100000000):08d}" if vendor == "711proxy" else secrets.token_hex(4)
                old = prefix + previous
                if self.job["child_proxy"].count(old) != 1:
                    raise EndChildAttempt({"code": "protocol_failed"})
                endpoint = self.job["child_proxy"].replace(old, prefix + next_ref, 1)
                try:
                    self.send("child_login_session_retry", {
                        "diagnostic": self.login_failure,
                        "child_proxy_previous_ref": previous, "child_proxy_next_ref": next_ref,
                    })
                except BaseException:
                    # Never use the previous endpoint after a possibly committed renewal.
                    raise EndChildAttempt({"code": "protocol_failed"}) from None
                self.job["child_proxy"], self.job["child_proxy_session_ref"] = endpoint, next_ref
                payload["proxy"] = endpoint
            self.login_failure = self.login_error = None
        flow = factory(job_id, payload)
        original_login = flow.login

        def login():
            self.in_login = True
            try:
                return original_login()
            except Exception as exc:
                detail = self.original_evidence(exc, diagnostics.stage(getattr(flow, "last_stage", "")), flow=flow)
                evidence = detail["evidence"]
                first = getattr(flow, "first_failed_request", None)
                first = first if isinstance(first, dict) else {}
                # Response markers remain diagnostic facts, not a veto on a
                # session renewal that the existing login retry policy permits.
                challenge = bool(first.get("body_markers")) or (first.get("headers") or {}).get("cf-mitigated") == "challenge"
                typed403 = isinstance(exc, core.upstream.LoginFlowError) and exc.status == 403 and exc.retryable
                proxy403 = evidence.get("proxy_http_status") == 403
                if (typed403 or proxy403) and evidence.get("stage") in {
                    "login", "csrf", "signin", "authorize", "identifier", "auth_session_read",
                    "password", "mfa_challenge", "mfa_verify", "workspace", "callback", "session"
                }:
                    if challenge:
                        evidence["challenge"] = True
                    self.login_failure = {"code": "login_proxy_session_failed", "retryable": True, **detail}
                    self.login_error = core.upstream.LoginFlowError(
                        "Login proxy session unavailable", code="login_proxy_session_failed", status=403, retryable=True)
                    raise self.login_error from None
                raise
            finally:
                self.in_login = False

        flow.login = login
        return flow

    def evidence(self, exc, operation, side="child", flow=None):
        if exc is self.login_error and self.login_failure is not None:
            return {k: v for k, v in self.login_failure.items() if k not in {"code", "retryable"}}
        detail = self.original_evidence(exc, operation, side, flow)
        if self.in_login and side == "child" and detail["evidence"].get("proxy_http_status") == 403:
            # Let the login wrapper request same-provider session renewal; this
            # response must not trigger the global network-cooldown boundary.
            return detail
        if not child_network_failure(detail):
            return detail
        if self.failure is None:
            mode = self.job.get("recovery_mode")
            code = ("fallback_unavailable" if mode == "fallback" else
                    "verification_unavailable" if mode == "verify" else
                    "export_uncertain" if operation == "export" else "login_failed")
            detail["evidence"]["reason"] = "network_error"
            self.failure = {"code": code, "retryable": True, **detail}
            try:
                self.send("child_proxy_network_failure", {"diagnostic": self.failure})
            except BaseException:
                # No acknowledgement means no further upstream work is allowed.
                # If the commit succeeded but its ACK was lost, recovery still
                # sees the persisted switch flag and the original submission fence.
                raise EndChildAttempt(self.failure) from None
        if operation != "export":
            raise EndChildAttempt(self.failure)
        # Preserve the core's curl/TLS proof distinguishing not-sent from unknown.
        # This path has already made its sole POST; it never retries transport.
        return detail

    def checkpoint(self, method, params=None, **kwargs):
        params = {**(params or {}), **kwargs}
        if method == "diagnostic":
            if self.send is core.rpc:
                print(json.dumps({"type": "diagnostic", **params["diagnostic"]}), flush=True)
                return {}
            return self.send(method, params)
        if self.failure is not None and method != "merge_v2_rejected":
            raise EndChildAttempt(self.failure)
        result = self.send(method, params)
        if self.failure is not None:
            # The core has durably established that export was not sent. Stop
            # before its next local login attempt even when recovery is disabled.
            raise EndChildAttempt(params.get("diagnostic") or self.failure)
        return result


def execute(job, checkpoint=core.rpc, **kwargs):
    boundary = ChildProxyBoundary(job, checkpoint)
    diagnostics.exception_evidence = boundary.evidence
    try:
        if job.get("recovery_mode", "") in {"", "retry"} and job.get("step") in {"assigned", "inviting", "invited", "login", "ready"}:
            factory = kwargs.pop("login_factory", core.CustomerLogin)
            kwargs["login_factory"] = lambda *args: boundary.login_factory(factory, *args)
        result = core.execute(job, checkpoint=boundary.checkpoint, **kwargs)
        if boundary.login_failure is not None and result.get("code") == "login_failed":
            result.update(boundary.login_failure)
        return result
    except EndChildAttempt as exc:
        return {"ok": False, **exc.diagnostic}
    finally:
        diagnostics.exception_evidence = boundary.original_evidence


if __name__ == "__main__":
    if any(arg in sys.argv for arg in ("--check", "--probe-mother", "--probe-proxy")):
        runpy.run_path(str(RUNTIME / "refund_runner.py"), run_name="__main__")
    else:
        try:
            result = execute(json.loads(sys.stdin.readline()))
        except BaseException:
            result = {"ok": False, "code": "protocol_failed"}
        print(json.dumps({"type": "result", **result}), flush=True)
