"""Isolated loopback upstream: configured response followed by personal Free."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "runtime"))
import refund_runner
from test_refund_transport import ProxyFlow
from test_refund_plan_fallback import PersonalProxyFlow

job=json.loads(sys.stdin.readline())
checking=job.get('recovery_mode')=='fallback'
fixture=PersonalProxyFlow(job,'chatgptfreeplan') if checking else ProxyFlow(job,export_status=409,export_body={'error':{'code':'unknown'}})
with fixture:
    result=refund_runner.execute(job)
    assert not fixture.errors
    posts=[r for r in fixture.records if r[2].endswith('export_and_deactivate')]
    assert len(posts)==(0 if checking else 1)
    if checking:assert result['ok']
    else:assert result['code']=='export_verification_required'
print(json.dumps({'type':'result',**result}),flush=True)
