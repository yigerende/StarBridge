import asyncio
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend import registration_gateway as gateway
from backend.common import BusinessError, password_matches


class RegistrationGatewayTests(unittest.TestCase):
    def tearDown(self):
        asyncio.run(gateway.close_transport())

    def test_path_preserves_query_without_allowing_url_escape(self):
        env={'REGISTRATION_URL':'http://registration-app:3210','REGISTRATION_GATEWAY_SECRET':'s'*40}
        with patch.dict('os.environ',env):
            for raw in (b'/registration//evil.test',b'/registration/%2e%2e/secrets',b'/registration/a%5cb'):
                request=Request({'type':'http','raw_path':raw,'query_string':b''})
                with self.assertRaises(BusinessError):gateway.destination(request)
            request=Request({'type':'http','raw_path':b'/registration/api/v1/runs/x/events','query_string':b'tail=1&reveal=1'})
            self.assertEqual(gateway.destination(request),'http://registration-app:3210/api/v1/runs/x/events?tail=1&reveal=1')

    def test_gateway_forwards_credentials_only_to_configured_service_and_preserves_download(self):
        calls=[]
        class Download(httpx.AsyncByteStream):
            async def __aiter__(self):yield b'synthetic-export'
        def handler(request):
            calls.append(request)
            return httpx.Response(200,stream=Download(),headers={'Content-Type':'application/zip','Content-Disposition':'attachment; filename="accounts.zip"'})
        real=httpx.AsyncClient
        def client(**kwargs):return real(transport=httpx.MockTransport(handler),**kwargs)
        app=FastAPI();app.include_router(gateway.router)
        with patch.dict('os.environ',{'REGISTRATION_URL':'http://registration-app:3210','REGISTRATION_GATEWAY_SECRET':'s'*40}),patch.object(gateway,'ip',return_value='127.0.0.1'),patch.object(gateway.httpx,'AsyncClient',client):
            response=TestClient(app).get('/registration/api/user/v1/accounts/export',headers={'Cookie':'xupay_session=test','x-registration-gateway':'forged','x-registration-source-ip':'8.8.8.8'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.content,b'synthetic-export')
        self.assertEqual(response.headers['content-disposition'],'attachment; filename="accounts.zip"')
        self.assertEqual(calls[0].headers['x-registration-gateway'],'s'*40)
        self.assertEqual(calls[0].headers['x-registration-source-ip'],'127.0.0.1')
        self.assertEqual(calls[0].headers['cookie'],'xupay_session=test')

    def test_unavailable_registration_returns_controlled_error(self):
        request=Request({'type':'http','method':'GET','raw_path':b'/registration/api/public','query_string':b'',
                         'headers':[(b'host',b'example.test')],'client':('127.0.0.1',5000)})
        request._body=b''
        with patch.dict('os.environ',{'REGISTRATION_URL':'http://registration-app:3210','REGISTRATION_GATEWAY_SECRET':'s'*40}),patch.object(gateway,'ip',return_value='127.0.0.1'),patch.object(gateway.httpx.AsyncClient,'send',side_effect=httpx.ConnectError('offline')):
            with self.assertRaises(BusinessError) as error:asyncio.run(gateway.registration(request,'api/public'))
        self.assertEqual(error.exception.status,503)

    def test_browser_navigation_closes_upstream_without_server_error(self):
        from starlette.requests import ClientDisconnect
        request=Request({'type':'http','method':'GET','path':'/registration/api/public','raw_path':b'/registration/api/public','headers':[(b'host',b'local')],'query_string':b''})
        with patch.dict('os.environ',{'REGISTRATION_URL':'http://registration-app:3210','REGISTRATION_GATEWAY_SECRET':'s'*40}),patch.object(gateway,'ip',return_value='127.0.0.1'),patch.object(request,'body',side_effect=ClientDisconnect),patch.object(gateway.httpx.AsyncClient,'aclose',new_callable=AsyncMock) as close:
            response=asyncio.run(gateway.registration(request,'api/public'))
        self.assertEqual(response.status_code,499)
        close.assert_not_awaited()


if __name__=='__main__':unittest.main()
