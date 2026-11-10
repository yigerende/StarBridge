"""Test-only real Python protocol behind two isolated loopback proxy fixtures."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "runtime"))
import refund_runner
from test_refund_transport import ProxyFlow

job = json.loads(sys.stdin.readline())
resuming = job["step"] in {"accepted", "removing"}
verifying = job.get("recovery_mode") == "verify"
assert job["child_proxy"] == ("" if resuming else "http://child:fixture@child.invalid:8080")
assert job["mother_proxy"] == "http://mother:fixture@mother.invalid:8081"
cleanup_failure = job["account"]["email"].startswith("cleanup") and not resuming
unknown = job["account"]["email"].startswith("unknown")
with ProxyFlow(job, remove_failure=cleanup_failure, export_body={} if unknown else None) as fixture:
    if resuming or verifying:
        fixture.members = True
    result = refund_runner.execute(job)
    assert not fixture.errors
    posts = [r for r in fixture.records if r[2].endswith("export_and_deactivate")]
    assert len(posts) == (0 if resuming or verifying else 1)
    if unknown:
        assert fixture.members and not any(r[1] == "DELETE" for r in fixture.records)
    if resuming:
        assert all(r[0] == "mother" for r in fixture.records)
print(json.dumps({"type": "result", **result}), flush=True)
