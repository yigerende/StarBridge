"""StarBridge's durable child-proxy boundary around the unchanged HUIXU runner.

Each worker attempt owns one subprocess. Transport evidence is intercepted before
the runtime's local retry/optional-observation handlers can discard it. Cooling
uses an acknowledged checkpoint; the asynchronous diagnostic queue stays passive.
"""
import json
from pathlib import Path
import runpy
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

    def evidence(self, exc, operation, side="child", flow=None):
        detail = self.original_evidence(exc, operation, side, flow)
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
        return core.execute(job, checkpoint=boundary.checkpoint, **kwargs)
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
