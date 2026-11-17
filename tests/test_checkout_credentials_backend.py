"""Mixin for the disposable-database subscription integration harness."""
import json
import os
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from backend import db, checkout_credentials, subscription_cslive_go as flow, cslive_go_post_payment as followup
from backend.checkout_errors import CheckoutError
from backend.common import decrypt, encrypt

BASE = '__Secure-next-auth.session-token'


def snapshot(creds, revision):
    return {'access_token': 'synthetic-at-'+revision, 'session_token': 'synthetic-cookie-'+revision,
            'account_id': creds['account_id'], 'email': creds['email'],
            'cookies': BASE+'=synthetic-cookie-'+revision}


class CheckoutCredentialBackendTests:
    def test_checkout_saved_session_survives_process_crash_or_timeout(self):
        from backend import checkout_process
        for ending in ('os._exit(7)', 'time.sleep(20)'):
            with self.subTest(ending=ending), self.retry_fixture(flow,fixed_proxy=True) as job, self.retry_network(flow) as (generate,stripe,finish):
                creds=json.loads(decrypt(job['credentials_enc']))
                frame=snapshot(creds,'before-child-exit')
                body=("import sys,json,os,time\nsys.stdin.readline()\n"
                      +"print("+repr(json.dumps({'type':'credentials','credentials':frame}))+",flush=True)\n"
                      +"assert json.loads(sys.stdin.readline()) == {'ok':True}\n"+ending)
                with patch.object(checkout_process.checkout_diagnostics,'submit'), self.assertRaises(CheckoutError):
                    checkout_process.run([sys.executable,'-u','-c',body],'{}',cwd=str(Path(__file__).parent),
                                         environment=dict(os.environ),timeout=1,save=checkout_credentials.saver(job,creds))
                saved=json.loads(decrypt(db.scalar('SELECT credentials_enc FROM subscription_runs WHERE order_id=$1',job['id'])))
                self.assertEqual(saved['access_token'],'synthetic-at-before-child-exit')
                self.assertEqual(db.scalar('SELECT frozen FROM users WHERE id=$1',job['user_id']),job['fee'])
                # A restarted worker may still carry the original queue payload.
                # The execution entry must reload the canonical row regardless.
                good=generate.return_value
                def checkout(**kwargs):
                    self.assertEqual(kwargs['session']['access_token'],'synthetic-at-before-child-exit')
                    self.assertEqual(kwargs['session']['session_token'],'synthetic-cookie-before-child-exit')
                    return good
                generate.side_effect=checkout
                flow.run_subscription(job)
                stripe.confirm.assert_called_once()
                self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='charge'",job['id']),1)

    def test_concurrent_credential_saves_cannot_overwrite_newer_version(self):
        with self.retry_fixture(flow,fixed_proxy=True) as job:
            original=json.loads(decrypt(job['credentials_enc']))
            callbacks=[checkout_credentials.saver(job,dict(original)) for _ in range(16)]
            def attempt(n):
                try:
                    callbacks[n](snapshot(original,str(n)))
                    return n
                except CheckoutError:
                    return None
            with ThreadPoolExecutor(max_workers=16) as pool:
                results=list(pool.map(attempt,range(16)))
            winners=[n for n in results if n is not None]
            self.assertEqual(len(winners),1)
            stored=json.loads(decrypt(db.scalar('SELECT credentials_enc FROM subscription_runs WHERE order_id=$1',job['id'])))
            self.assertEqual(stored['access_token'],'synthetic-at-'+str(winners[0]))
            self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind IN ('charge','unfreeze')",job['id']),0)

    def test_checkout_refresh_survives_400_retry_and_settles_once(self):
        with self.retry_fixture(flow,fixed_proxy=True) as job, self.retry_network(flow) as (generate,stripe,finish):
            original=json.loads(decrypt(job['credentials_enc']))
            initial_expiry=job['credentials_expires_at']
            seen=[]
            good=generate.return_value
            def checkout(**kwargs):
                seen.append(kwargs['session'])
                kwargs['save_credentials'](snapshot(original,str(len(seen))))
                if len(seen)==1: raise CheckoutError('http_400')
                return good
            generate.side_effect=checkout
            flow.run_subscription(job)
            self.assertEqual(db.scalar('SELECT status FROM orders WHERE id=$1',job['id']),'queued')
            self.assertEqual(db.scalar('SELECT frozen FROM users WHERE id=$1',job['user_id']),job['fee'])
            stripe.confirm.assert_not_called()
            latest=json.loads(decrypt(db.scalar('SELECT credentials_enc FROM subscription_runs WHERE order_id=$1',job['id'])))
            self.assertEqual(latest['access_token'],'synthetic-at-1')
            self.assertEqual(latest['session_token'],'synthetic-cookie-1')
            self.assertEqual(db.scalar('SELECT credentials_expires_at FROM subscription_runs WHERE order_id=$1',job['id']),initial_expiry)
            # The restart releases its proxy. Provide a newly acquired synthetic
            # route without invoking a real national-pool supplier.
            db.execute("INSERT INTO order_proxy_sessions(order_id,resource_id,endpoint_enc,expires_at,source_mode) "
                       "VALUES($1,'fixture-retry',$2,now()+interval '1 hour','national_pool')",
                       job['id'], encrypt('http://fixture:secret@proxy.example.test:8080'))
            job2=self.ready_retry(flow,job['id'])
            flow.run_subscription(job2)
            self.assertEqual(seen[1]['access_token'],'synthetic-at-1')
            self.assertEqual(seen[1]['cookies'],{BASE:'synthetic-cookie-1'})
            latest=json.loads(decrypt(db.scalar('SELECT credentials_enc FROM subscription_runs WHERE order_id=$1',job['id'])))
            self.assertEqual(latest['access_token'],'synthetic-at-2')
            self.assertEqual(db.scalar('SELECT status FROM orders WHERE id=$1',job['id']),'succeeded')
            self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='charge'",job['id']),1)
            self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='unfreeze'",job['id']),0)
            stripe.confirm.assert_called_once()
            finish.assert_called_once()

    def test_checkout_refresh_rejects_stale_workers_and_payment_fences(self):
        with self.retry_fixture(flow,fixed_proxy=True) as job:
            creds=json.loads(decrypt(job['credentials_enc']))
            stale=checkout_credentials.saver(job,dict(creds))
            save=checkout_credentials.saver(job,creds)
            save(snapshot(creds,'1'))
            saved=db.scalar('SELECT credentials_enc FROM subscription_runs WHERE order_id=$1',job['id'])
            with self.assertRaises(CheckoutError): stale(snapshot(creds,'stale'))
            with self.assertRaises(CheckoutError): save(dict(snapshot(creds,'other'),account_id='other'))
            for clause, undo in (
                ('trace_round=trace_round+1','trace_round=trace_round-1'),
                ('payment_submit_started_at=now()','payment_submit_started_at=NULL'),
                ('paid_at=now()','paid_at=NULL'),
                ("credentials_expires_at=now()-interval '1 second'","credentials_expires_at=now()+interval '1 hour'")):
                db.execute('UPDATE subscription_runs SET '+clause+' WHERE order_id=$1',job['id'])
                with self.assertRaises(CheckoutError): save(snapshot(creds,'bad'))
                db.execute('UPDATE subscription_runs SET '+undo+' WHERE order_id=$1',job['id'])
            with patch.object(checkout_credentials,'encrypt',side_effect=RuntimeError('private db failure')):
                with self.assertRaises(CheckoutError): save(snapshot(creds,'bad'))
            self.assertEqual(db.scalar('SELECT credentials_enc FROM subscription_runs WHERE order_id=$1',job['id']),saved)
            self.assertEqual(creds['access_token'],'synthetic-at-1')

    def test_checkout_same_round_approval_uses_refreshed_at(self):
        with self.retry_fixture(flow,fixed_proxy=True) as job, self.retry_network(flow) as (generate,stripe,finish):
            creds=json.loads(decrypt(job['credentials_enc']))
            checkout_credentials.saver(job,creds)(snapshot(creds,'newer-than-claim'))
            good=generate.return_value
            def checkout(**kwargs):
                self.assertEqual(kwargs['session']['access_token'],'synthetic-at-newer-than-claim')
                kwargs['save_credentials'](snapshot(creds,'approval'))
                return good
            generate.side_effect=checkout
            stripe.confirm.return_value={'submission_attempt':{'state':'requires_approval'}}
            session=Mock()
            session.post.return_value.status_code=200
            session.post.return_value.headers={'content-type':'application/json'}
            session.post.return_value.json.return_value={'status':'approved'}
            with patch.object(flow,'payment_session',return_value=session), patch('backend.subscription_payment_result.poll_result',return_value=(True,'',100,{})):
                flow.run_subscription(job)
            self.assertEqual(session.post.call_args.kwargs['headers']['Authorization'],'Bearer synthetic-at-approval')
            self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='charge'",job['id']),1)

    def test_checkout_post_payment_initial_and_recovered_use_latest_saved_credentials(self):
        with self.retry_fixture(flow,fixed_proxy=True) as job:
            creds=json.loads(decrypt(job['credentials_enc']))
            checkout_credentials.saver(job,creds)(snapshot(creds,'before-payment'))
            db.execute("UPDATE orders SET status='succeeded' WHERE id=$1",job['id'])
            db.execute("UPDATE subscription_runs SET paid_at=now(),checkout_session_id='cs_fixture' WHERE order_id=$1",job['id'])
            seen=[]
            def execute(cp,save):
                seen.append(dict(cp['credentials']))
                cp['credentials'].update(access_token='synthetic-at-after-payment', session_token='synthetic-cookie-after-payment',
                                          cookies={BASE:'synthetic-cookie-after-payment'})
                save('activation')
            with patch.object(followup,'execute',side_effect=execute), patch('backend.post_payment_delay.wait_before_refresh',return_value=True):
                followup.finish_payment(job['id'],plan='plus')
                # Simulate a later canonical credential write with an older
                # progress snapshot: recovery must never resurrect that snapshot.
                latest=json.loads(decrypt(db.scalar('SELECT credentials_enc FROM subscription_runs WHERE order_id=$1',job['id'])))
                latest.update(access_token='synthetic-at-newest-order',session_token='synthetic-cookie-newest-order',cookies={BASE:'synthetic-cookie-newest-order'})
                db.execute('UPDATE subscription_runs SET credentials_enc=$2 WHERE order_id=$1',job['id'],encrypt(json.dumps(latest)))
                followup._run(job['id'])
            self.assertEqual(seen[0]['access_token'],'synthetic-at-before-payment')
            self.assertEqual(seen[0]['session_token'],'synthetic-cookie-before-payment')
            self.assertEqual(seen[1]['access_token'],'synthetic-at-newest-order')
            self.assertEqual(seen[1]['cookies'],{BASE:'synthetic-cookie-newest-order'})
