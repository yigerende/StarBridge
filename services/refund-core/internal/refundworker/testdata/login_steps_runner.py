"""Real worker/runtime chain using loopback-only login response fixtures."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "runtime"))
import refund_runner
from test_refund_transport import ProxyFlow

job = json.loads(sys.stdin.readline())
scenario = job["account"]["email"].split("@")[0]
mode = job.get("recovery_mode", "")


class LoginFixture(ProxyFlow):
    def reply(self, role, method, url, headers, raw):
        if url.path == "/api/accounts/authorize/continue" and (mode != "retry" or scenario == "verification_persistent"):
            body = {} if scenario == "incomplete" else {"page": {"type": "email_otp_verification"}}
            return 200, body, {}
        return super().reply(role, method, url, headers, raw)


with LoginFixture(job) as fixture:
    fixture.invited = bool(mode)
    result = refund_runner.execute(job)
    assert not fixture.errors
    posts = [r for r in fixture.records if r[2].endswith("export_and_deactivate")]
    assert len(posts) == (1 if mode == "retry" and scenario != "verification_persistent" else 0)
    if mode == "cleanup":
        assert all(r[0] == "mother" for r in fixture.records)
        assert not fixture.invited and not fixture.members
    elif mode != "retry" or scenario == "verification_persistent":
        assert result["code"] == ("login_response_incomplete" if scenario == "incomplete" else "extra_verification")
        assert not any(r[2].endswith("password/verify") for r in fixture.records)
print(json.dumps({"type": "result", **result}), flush=True)
