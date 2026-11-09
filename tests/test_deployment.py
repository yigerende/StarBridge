import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend import deployment
from backend.common import BusinessError, administrator
spec=importlib.util.spec_from_file_location('updater',ROOT/'scripts/starbridge_updater.py')
updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)


class DeploymentTests(unittest.TestCase):
    def test_host_rejects_shell_paths_credentials_stale_and_unknown_requests(self):
        good={'id':'a'*32,'action':'prepare','created_at':time.time(),'public_origin':'https://bridge.example.com:8443'}
        self.assertEqual(updater.validate_request(dict(good))['public_origin'],good['public_origin'])
        for value in ['https://example.com/route','https://user:pw@example.com','https://example.com?x=1',
                      'https://example.com\nAPP_VERSION=evil','https://$(id)','file:///tmp/test','https://example.com:0']:
            with self.subTest(value=value),self.assertRaises(updater.UpdateError):updater.validate_request({**good,'public_origin':value})
        for values in [{'id':'../../x'},{'action':'exec'},{'created_at':0},{'prepared_id':'../../x'}]:
            with self.assertRaises(updater.UpdateError):updater.validate_request({**good,**values})

    def test_simultaneous_update_requests_are_published_once(self):
        app=FastAPI();app.include_router(deployment.router)
        app.dependency_overrides[administrator]=lambda:None
        @app.exception_handler(BusinessError)
        async def business_error(request,error):return JSONResponse({'error':error.message},status_code=error.status)
        @contextmanager
        def transaction():yield Mock()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'requests').mkdir()
            (path/'heartbeat.json').write_text(json.dumps({'time':time.time()}))
            with patch.dict(os.environ,{'MAINTENANCE_DIR':folder}),patch.object(deployment.db,'transaction',transaction),patch.object(deployment,'audit'):
                def submit(_):return TestClient(app).post('/api/admin/system/update/prepare',json={'public_origin':'https://example.com'}).status_code
                with ThreadPoolExecutor(max_workers=20) as pool:statuses=list(pool.map(submit,range(20)))
            self.assertEqual(statuses.count(200),1)
            self.assertEqual(statuses.count(409),19)
            self.assertEqual(len(list((path/'requests').iterdir())),1)
            updater.validate_request(json.loads((path/'requests/request.json').read_text()))

    def test_web_requires_administrator_and_matching_prepared_version(self):
        app=FastAPI();app.include_router(deployment.router)
        @app.exception_handler(BusinessError)
        async def business_error(request,error):return JSONResponse({'error':error.message},status_code=error.status)
        def deny():raise BusinessError('管理员权限',403)
        app.dependency_overrides[administrator]=deny
        self.assertEqual(TestClient(app).get('/api/admin/system/update').status_code,403)
        app.dependency_overrides[administrator]=lambda:None
        with patch.object(deployment,'status',return_value={'available':True,'pending':False,'phase':'ready','prepared_id':'a'*32}):
            self.assertEqual(TestClient(app).post('/api/admin/system/update/restart',json={'prepared_id':'b'*32}).status_code,409)

    def test_health_failure_restores_images_without_database_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state=root/'state';state.mkdir();public=root/'public';public.mkdir()
            (root/'.env').write_text('APP_VERSION=old\nPUBLIC_ORIGIN=https://example.com\nMASTER_KEY=preserve\n')
            for name in ('compose.yml','compose.registration.yml'):(root/name).write_text('services: {}')
            previous={'id':'a'*32,'source':str(root),'version':'old','images':{'app':'sha256:old','registration-worker':'sha256:oldworker'},'epoch':1}
            prepared={**previous,'id':'b'*32,'version':'new','base':'old','origin':'https://new.example.com','images':{'app':'sha256:new','registration-worker':'sha256:newworker'}}
            with patch.object(updater,'ROOT',root),patch.object(updater,'STATE',state),patch.object(updater,'PUBLIC',public):
                service=updater.Updater();service.private['prepared']=prepared
                service.current=Mock(return_value='old');service.wait_idle=Mock();service.backup=Mock(return_value=previous)
                service.run=Mock(return_value='');service.health=Mock(side_effect=updater.UpdateError('unhealthy'));service.restore=Mock()
                with self.assertRaisesRegex(updater.UpdateError,'已恢复先前'):service.restart({'id':'c'*32,'prepared_id':'b'*32})
                service.restore.assert_called_once_with(previous)
                self.assertNotIn('journal',service.private)
                commands=[call.args[0] for call in service.run.call_args_list]
                self.assertFalse(any('pg_restore' in command for command in commands))
                switch=next(c for c in commands if 'up' in c)
                self.assertEqual(switch[-2:],['app','registration-worker'])
                self.assertIn('--no-build',switch);self.assertIn('--no-deps',switch)


if __name__=='__main__':unittest.main()
