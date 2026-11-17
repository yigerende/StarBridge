"""No database, app startup, or live upstream access."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from backend import checkout_diagnostics as diagnostics


def frame(events=None):
    return json.dumps({'starbridge_checkout_diagnostics': 1, 'events': events or [
        {'event': 'session_refresh', 'outcome': 'failed', 'response_error': 'RefreshAccessTokenError'}], 'dropped': 0}).encode()


class AsyncCheckoutDiagnosticTests(unittest.TestCase):
    def test_stalled_disk_and_full_queue_never_block_submitters(self):
        entered, release = threading.Event(), threading.Event()
        records = []
        def slow_sink(record):
            entered.set()
            release.wait(10)
            records.append(record)
        writer = diagnostics.AsyncDiagnostics(capacity=8, sink=slow_sink)
        writer.start()
        try:
            writer.submit('first', 1, frame())
            self.assertTrue(entered.wait(2))
            started = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
                list(executor.map(lambda n: writer.submit('order'+str(n), n % 3+1, frame()), range(1000)))
            elapsed = time.perf_counter()-started
            self.assertLess(elapsed, 2)
            self.assertEqual(writer.queue.qsize(), 8)
            self.assertEqual(writer.dropped, 992)
            print(json.dumps({'concurrent_submitters':50, 'diagnostic_submissions':1000,
                              'blocked_sink_submit_seconds':round(elapsed,4), 'queue_dropped':writer.dropped}))
        finally:
            release.set(); writer.stop(5)
        self.assertEqual(len(records), 9)
        self.assertEqual(records[-1]['queue_dropped'], 992)

    def test_only_typed_allowlisted_evidence_is_written(self):
        secret = 'DO-NOT-PERSIST-ACCESS-TOKEN-OR-PROXY'
        raw = frame([{'event':['bad']}, {'event':'http_request', 'step':'checkout_create', 'status':400,
                     'responseErrorCode':'invalid_request_error', 'responseMessage':secret, 'url':secret,
                     'headers':{'Authorization':secret}, 'cookies':secret, 'durationMs':12.5},
                    {'event':'session_refresh', 'response_error':secret, 'response_has_at':'yes',
                     'at_version':secret, 'before_at_version':'a'*24, 'response_has_error':True}])
        result = diagnostics.decode(b'private stderr '+secret.encode()+b'\n'+raw)
        encoded = json.dumps(result)
        self.assertNotIn(secret, encoded)
        self.assertNotIn('headers', encoded)
        self.assertNotIn('response_has_at', encoded)
        self.assertEqual(result['events'][0]['status'],400)
        self.assertEqual(result['events'][1]['response_error'],'redacted_error')
        for malformed in (b'bad',b'{"starbridge_checkout_diagnostics":1,"events":null}', b'{}'):
            self.assertIsNone(diagnostics.decode(malformed))

    def test_write_failures_recover_without_payload_or_business_fallback(self):
        records=[]
        def sink(record):
            if not records:
                records.append('failed')
                raise OSError('PRIVATE FAILURE TEXT')
            records.append(record)
        writer=diagnostics.AsyncDiagnostics(sink=sink)
        with self.assertLogs('starbridge.checkout_diagnostics',level='WARNING') as logs:
            writer.start()
            writer.submit('one',1,frame());writer.submit('two',2,frame());writer.stop(5)
        self.assertEqual(writer.write_failures,1)
        self.assertEqual(writer.written,1)
        self.assertNotIn('PRIVATE FAILURE TEXT',' '.join(logs.output))
        self.assertEqual(records[1]['order_id'],'two')

    def test_private_file_and_graceful_drain(self):
        with tempfile.TemporaryDirectory() as directory:
            writer=diagnostics.AsyncDiagnostics(directory=directory)
            writer.start()
            for n in range(20):writer.submit('order'+str(n),n+1,frame())
            writer.stop(5)
            path=Path(directory)/'diagnostics/checkout.jsonl'
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(rows),20)
            self.assertEqual([r['round'] for r in rows],list(range(1,21)))
            if os.name!='nt':self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_fingerprints_are_scoped_to_order_and_master_key(self):
        with patch.dict(os.environ,{'MASTER_KEY':'synthetic-only'}):
            one=diagnostics.diagnostic_key('order-1')
            self.assertEqual(one,diagnostics.diagnostic_key('order-1'))
            self.assertNotEqual(one,diagnostics.diagnostic_key('order-2'))
            self.assertEqual(len(one),64)
            self.assertEqual(diagnostics.diagnostic_key('invalid/identifier'),'')

    def test_adapter_result_and_error_survive_diagnostic_sink_failure(self):
        from backend import cslive_go_checkout as adapter
        from backend.checkout_errors import CheckoutError
        args=dict(session={'access_token':'private','session_token':'private','account_id':'acct'},country='PH',plan='pro5x',proxy_url='http://proxy.invalid:3000')
        with patch.object(adapter,'binary_path',return_value=Mock(is_file=lambda:True)), \
             patch.object(adapter.trace,'request',return_value=MockContext()), \
             patch('backend.payment_trace.diagnostic_identity',return_value=('order-1',1)), \
             patch.object(diagnostics.writer,'submit',side_effect=RuntimeError('disk failed')):
            for payload, code in [({'error_code':'http_400'},'http_400'),({'error_code':'session_expired'},'session_expired')]:
                result=subprocess.CompletedProcess([],1,json.dumps(payload).encode(),frame())
                with patch.object(adapter.subprocess,'run',return_value=result):
                    with self.assertRaises(CheckoutError) as error:adapter.create_checkout(**args)
                    self.assertEqual(error.exception.code,code)
            payload={'checkout_id':'cs_live_fixture','checkout_url':'https://checkout.example.test/fixture'}
            with patch.object(adapter.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(payload).encode(),frame())):
                self.assertTrue(adapter.create_checkout(**args)['ok'])


class MockContext:
    def __enter__(self):return Mock()
    def __exit__(self,*args):return False


if __name__=='__main__':unittest.main()
