import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import checkout_diagnostics as d, checkout_evidence as index


def frame(events):
    return json.dumps({'starbridge_checkout_diagnostics':1,'events':events}).encode()


class CheckoutEvidenceTests(unittest.TestCase):
    def test_specific_errors_and_request_versions_survive_without_secrets(self):
        raw=frame([{'event':'http_request','step':'checkout_create','status':400,
            'upstream_code':'checkout_account_not_eligible','upstream_type':'invalid_request_error',
            'upstream_message':'Account cannot purchase this plan. user@example.test https://secret.test/x Bearer private-value',
            'upstream_detail':'password=secret-value','upstream_request_id':'req_fixture123'},
            {'event':'wire_credentials','step':'account_check','at_version':'a'*24,'auth_cookie_version':'b'*24,'duplicate_auth_cookies':True},
            {'event':'credentials','point':'refresh_return','proxy_version':'c'*24}])
        data=d.decode(raw);encoded=json.dumps(data)
        self.assertEqual(data['events'][0]['upstream_code'],'checkout_account_not_eligible')
        self.assertIn('Account cannot purchase this plan.',encoded)
        for secret in ('user@example.test','https://secret.test','private-value','secret-value'):self.assertNotIn(secret,encoded)
        self.assertTrue(data['events'][1]['duplicate_auth_cookies'])
        self.assertEqual(data['events'][2]['proxy_version'],'c'*24)
        self.assertEqual(d._event({'event':'http_request','upstream_code':'sk_secret'})['upstream_code'],'redacted')

    def test_index_retains_round_order_isolation_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as root:
            writer=d.AsyncDiagnostics(directory=root);writer.start()
            for number in (2,1,3):
                writer.submit('first',number,frame([{'event':'http_request','step':'checkout_create','status':400}]))
            writer.submit('second',1,frame([{'event':'session_refresh','outcome':'failed','response_error':'RefreshAccessTokenError'}]))
            writer.stop(5)
            self.assertEqual(writer.write_failures,0)
            self.assertEqual([r['round'] for r in index.read('first',root)],[1,2,3])
            self.assertEqual(index.read('second',root)[0]['events'][0]['response_error'],'RefreshAccessTokenError')
            self.assertEqual(index.read('../first',root),[])
            path=Path(root)/'diagnostics/orders/first.json'
            path.write_text('{broken')
            self.assertEqual(index.read('first',root),[])

    def test_index_is_bounded_and_bad_disk_never_changes_adapter_result(self):
        with tempfile.TemporaryDirectory() as root, patch.object(index,'MAX_ORDERS',3):
            directory=Path(root)/'diagnostics';directory.mkdir()
            failure=[{'event':'transaction','outcome':'failed'}]
            index.save(directory,{'order_id':'normal-success','round':1,'events':[{'event':'transaction','outcome':'succeeded'}]})
            self.assertFalse((directory/'orders/normal-success.json').exists())
            for n in range(5):index.save(directory,{'order_id':f'order{n}','round':1,'events':failure})
            self.assertEqual(len(list((directory/'orders').glob('*.json'))),3)
            for n in range(9):index.save(directory,{'order_id':'last','round':n,'events':failure})
            self.assertEqual([r['round'] for r in index.read('last',root)],[0,6,7,8])
        with tempfile.TemporaryDirectory() as root, patch.object(index,'save',side_effect=OSError('private-disk-error')):
            writer=d.AsyncDiagnostics(directory=root);writer.start()
            with self.assertLogs('starbridge.checkout_diagnostics',level='WARNING') as logs:
                writer.submit('first',1,frame([{'event':'transaction','outcome':'failed'}]));writer.stop(5)
            self.assertEqual(writer.write_failures,1)
            self.assertNotIn('private-disk-error',' '.join(logs.output))

    def test_admin_endpoint_denies_customer_api_and_anonymous_before_read(self):
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient
        from backend.records import admin_router
        from backend import common
        app=FastAPI();app.include_router(admin_router)
        @app.exception_handler(common.BusinessError)
        async def handle(request, exc):return JSONResponse({'error':'denied'},status_code=exc.status)
        with TestClient(app) as client, patch('backend.records.db.one',return_value={'id':'fixture'}), \
             patch('backend.common.verified_identity') as identity, patch('backend.common.event'), \
             patch('backend.rate_limits.enforce'), patch.object(index,'read',return_value=[]) as read:
            for user,code in ((None,401),({'status':'active','role':'member'},403)):
                identity.return_value=user
                self.assertEqual(client.get('/api/admin/orders/fixture/checkout-diagnostics').status_code,code)
            self.assertEqual(client.get('/api/admin/orders/fixture/checkout-diagnostics',headers={'authorization':'Bearer test'}).status_code,403)
            read.assert_not_called()
            identity.return_value={'status':'active','role':'admin'}
            result=client.get('/api/admin/orders/fixture/checkout-diagnostics')
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.headers['cache-control'],'no-store')
            self.assertEqual(result.json(),{'rounds':[]})


if __name__=='__main__':unittest.main()
