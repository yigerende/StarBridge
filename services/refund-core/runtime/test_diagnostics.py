import io
import json
import unittest
from enum import IntEnum
from unittest.mock import Mock, patch

import diagnostics as d
import refund_runner as runner
from manager_oauth import upstream
import test_refund_runner


class DiagnosticTests(unittest.TestCase):
    def test_curl_enum_codes_and_malformed_error_code(self):
        class CurlCode(IntEnum):
            CONNECT = 7
        exc=RuntimeError('private-message');exc.code=CurlCode.CONNECT
        self.assertEqual(d.exception_evidence(exc,'login')['network_code'],7)
        exc.code={'private':'token'}
        self.assertEqual(d.exception_evidence(exc,'login')['evidence']['reason'],'unknown_error')

    def test_nested_network_and_typed_error_preserve_reason_without_secret(self):
        try:
            inner=RuntimeError('http://user:private-password@proxy.invalid Bearer private-token')
            inner.code=7
            raise RuntimeError('CONNECT tunnel failed, response 504 private-password') from inner
        except Exception as exc:
            result=d.exception_evidence(exc,'csrf')
        self.assertEqual(result['network_code'],7)
        self.assertEqual(result['evidence']['proxy_http_status'],504)
        self.assertEqual(result['evidence']['reason'],'network_error')
        self.assertNotIn('private',json.dumps(result))
        result=d.exception_evidence(upstream.LoginFlowError('private-token',code='csrf_or_risk_blocked',status=403),'csrf')
        self.assertEqual(result['http_status'],403)
        self.assertEqual(result['evidence']['reason'],'csrf_or_risk_blocked')

    def test_response_allowlist_keeps_challenge_not_body_or_headers(self):
        response=Mock(status_code=403,text='<html>challenge-platform private-token private-password</html>',headers={'Set-Cookie':'private-cookie','retry-after':'60'})
        response.json.side_effect=ValueError('private-response')
        rows=[]
        with d.capture(lambda _,p:rows.append(p)):
            d.request_event('csrf','response',response=response)
        out=rows[0]['diagnostic']
        self.assertTrue(out['evidence']['challenge'])
        self.assertEqual(out['evidence']['retry_after_seconds'],60)
        self.assertEqual(out['evidence']['body_kind'],'html')
        self.assertNotIn('private',json.dumps(rows))

    def test_capture_is_bounded_and_resets_between_attempts(self):
        rows=[]
        with d.capture(lambda _,p:rows.append(p)):
            for _ in range(300):d.record({'stage':'csrf','event':'error'})
        self.assertEqual(len(rows),128)
        self.assertEqual(rows[-1]['diagnostic']['evidence']['event'],'truncated')
        with d.capture(lambda _,p:rows.append(p)):d.record({'stage':'login'})
        self.assertEqual(rows[-1]['diagnostic']['evidence']['sequence'],1)

    def test_login_failure_evidence_survives_final_result(self):
        job=test_refund_runner.RunnerTests().job();job['settings']['recovery']={'enabled':True}
        login=Mock(last_status=403,last_stage='api_auth_csrf')
        login.login.side_effect=upstream.LoginFlowError('private-password',code='csrf_or_risk_blocked',status=403)
        rows=[]
        result=runner.execute(job,lambda method,*a:rows.append((method,a)),lambda _:Mock(),lambda *_:login)
        self.assertEqual(result['code'],'login_failed')
        self.assertEqual(result['http_status'],403)
        self.assertEqual(result['evidence']['stage'],'csrf')
        self.assertTrue(any(m=='diagnostic' for m,_ in rows))
        self.assertNotIn('private-password',str((rows,result)))

    def test_protocol_observations_do_not_wait_for_rpc_ack(self):
        # The real wire sink prints telemetry; only business checkpoints read ACKs.
        job=test_refund_runner.RunnerTests().job('accepted')
        mother=Mock()
        def remove(_):d.record({'stage':'member_remove','event':'start'})
        mother.remove.side_effect=remove
        with patch('sys.stdin',io.StringIO('{"ok":true}\n{"ok":true}\n')),patch('sys.stdout',new_callable=io.StringIO) as output:
            result=runner.execute(job,mother_factory=lambda _:mother)
        self.assertTrue(result['ok'])
        wire=[json.loads(v) for v in output.getvalue().splitlines()]
        self.assertEqual([v['type'] for v in wire],['checkpoint','diagnostic','checkpoint'])

if __name__=='__main__':unittest.main()
