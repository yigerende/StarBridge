"""Synthetic subprocesses and credential snapshots only; no upstream calls."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import checkout_credentials as credentials, checkout_process
from backend.cslive_go_checkout import _build_session_json
from backend.checkout_errors import CheckoutError

BASE = '__Secure-next-auth.session-token'


class CheckoutCredentialTests(unittest.TestCase):
    def test_rotated_cookie_and_chunks_override_old_token(self):
        prior = dict(access_token='old-at', session_token='old-cookie', account_id='acct', email='a@example.test',
                     cookies={BASE:'old-cookie'}, team_promo_code='fixture', token_expires_at='old-expiry')
        updated = credentials.refreshed_credentials(prior, dict(access_token='new-at', session_token='new-cookie',
            account_id='acct', email='a@example.test', cookies=f'{BASE}.1=cookie; {BASE}.0=new-; oai-did=device'))
        self.assertEqual(updated['cookies'], {BASE+'.1':'cookie',BASE+'.0':'new-'})
        self.assertEqual(updated['session_token'], 'new-cookie')
        self.assertEqual(updated['team_promo_code'], 'fixture')
        self.assertNotIn('token_expires_at', updated)
        payload = json.loads(_build_session_json(dict(updated,session_token='stale')))
        self.assertEqual((payload['accessToken'],payload['sessionToken']),('new-at','new-cookie'))
        self.assertEqual(prior['access_token'],'old-at')
        self.assertEqual(credentials.session_token({BASE+'.0':'a',BASE+'.2':'b'}),'')

    def test_invalid_credentials_never_replace_previous(self):
        prior = dict(access_token='old-at',account_id='acct',email='a@example.test')
        good = dict(access_token='new-at',session_token='new-cookie',account_id='acct',email='a@example.test',cookies=f'{BASE}=new-cookie')
        for changes in ({'account_id':'other'},{'email':'other@example.test'},{'access_token':''},
                        {'cookies':'tracking=value'},{'session_token':'stale'},{'cookies':f'{BASE}=x\nBAD'},
                        {'cookies':f'{BASE}.1=new-cookie'}):
            with self.subTest(changes=list(changes)), self.assertRaises(CheckoutError):
                credentials.refreshed_credentials(prior,dict(good,**changes))
        self.assertEqual(prior['access_token'],'old-at')

    def run_child(self, body, save, timeout=5):
        return checkout_process.run([sys.executable,'-u','-c',body], '{}', cwd=str(Path(__file__).parent),
                                    environment=dict(os.environ), timeout=timeout, save=save)

    def test_bridge_saves_before_ack_and_excludes_private_frame_from_result(self):
        body = '''import sys,json
json.loads(sys.stdin.readline())
print(json.dumps({'type':'credentials','credentials':{'access_token':'PRIVATE'}}),flush=True)
ack=json.loads(sys.stdin.readline())
assert ack == {'ok':True}
print(json.dumps({'error_code':'http_400'}),flush=True)
'''
        saved=[]
        with patch.object(checkout_process.checkout_diagnostics,'submit') as diagnostic:
            result=self.run_child(body,saved.append)
        self.assertEqual(saved,[{'access_token':'PRIVATE'}])
        self.assertEqual(json.loads(result.stdout),{'error_code':'http_400'})
        self.assertNotIn(b'PRIVATE',result.stdout)
        self.assertNotIn('PRIVATE',str(diagnostic.call_args_list))

    def test_bridge_reaps_failed_saves_malformed_output_and_timeouts(self):
        emit="import sys,json,time\nsys.stdin.readline()\n"
        for body,save,code in (
            (emit+"print(json.dumps({'type':'credentials','credentials':{}}),flush=True)\nsys.stdin.readline()\ntime.sleep(20)",
             lambda _: (_ for _ in ()).throw(ValueError('PRIVATE')), 'checkpoint_failed'),
            (emit+"print('bad PRIVATE',flush=True)",lambda _:None,'invalid_response'),
            (emit+"time.sleep(20)",lambda _:None,'timeout'),
            (emit+"print('x'*1048577,flush=True)",lambda _:None,'invalid_response')):
            with self.subTest(code=code), patch.object(checkout_process.checkout_diagnostics,'submit'):
                with self.assertRaises(CheckoutError) as error:
                    self.run_child(body,save,timeout=0.3)
                self.assertEqual(str(error.exception),code)


if __name__=='__main__': unittest.main()
