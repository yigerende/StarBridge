"""Regression cases for lost payment responses and delayed paid follow-ups."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import requests

from backend import db, subscription, subscription_cslive_go, subscription_recovery as recovery
from backend import subscription_payment_result as result
from backend.common import decrypt, now
from backend import subscription_retry
from test_subscription_payment_result import response, PAID, DECLINED

INACTIVE = {'error': {'type': 'invalid_request_error', 'code': 'checkout_not_active_session',
                       'message': 'This Checkout Session is no longer active.'}}


class SubscriptionRecoveryTests:
    def recovery_due(self, order_id):
        db.execute("UPDATE subscription_recoveries SET next_at=now()-interval '1 second' WHERE order_id=$1", order_id)

    def finish_recovered(self, order_id, **kwargs):
        with db.transaction() as q:
            q.execute("UPDATE orders SET activation='active',renewal='disabled' WHERE id=$1", order_id)
            recovery.followup_saved(q, order_id, 'completed', '')
        return {'state': 'completed'}

    def test_recovery_inactive_checkout_uses_original_state_not_another_payment(self):
        for module in (subscription, subscription_cslive_go):
            with self.subTest(flow=module.FLOW), self.retry_fixture(module) as job, self.retry_network(module) as (_, stripe, tail):
                stripe.confirm.side_effect = requests.ReadTimeout()
                stripe.session.get.return_value = response(INACTIVE, 400)
                stripe.session.post.return_value = response(dict(PAID, id='cs_test_retry'))
                module.run_subscription(job)
                self.assertEqual(self.payment_api(job)['state'], 'succeeded')
                stripe.confirm.assert_called_once()
                args, kwargs = stripe.session.post.call_args
                self.assertTrue(args[0].endswith('/cs_test_retry/init'))
                self.assertEqual(set(kwargs['data']), {'key', 'eid', 'browser_locale', 'browser_timezone', 'redirect_type'})
                self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='charge'", job['id']), 1)

    def test_recovery_original_intent_and_closed_unpaid_checkout(self):
        for variant in ('paid_intent', 'wrong_intent', 'closed_unpaid', 'wrong_checkout'):
            with self.subTest(variant=variant), self.retry_fixture(subscription_cslive_go) as job, self.retry_network(subscription_cslive_go) as (_, stripe, tail):
                stripe.init.return_value.update(eid='original-eid', payment_intent={
                    'id': 'pi_Original', 'client_secret': 'pi_Original_secret_Original'})
                stripe.confirm.side_effect = requests.ReadTimeout()
                def get(url, **kwargs):
                    if '/payment_pages/' in url:
                        self.assertEqual(kwargs['params']['eid'], 'original-eid')
                        return response(INACTIVE, 400)
                    self.assertTrue(url.endswith('/payment_intents/pi_Original'))
                    self.assertEqual(kwargs['params']['client_secret'], 'pi_Original_secret_Original')
                    return response({'id': 'pi_Original' if variant=='paid_intent' else 'pi_Other',
                                     'status': 'succeeded', 'amount_received': 1234, 'currency': 'usd'})
                stripe.session.get.side_effect = get
                stripe.session.post.return_value = response({'id': 'cs_other' if variant=='wrong_checkout' else 'cs_test_retry',
                    'status': 'complete', 'payment_status': 'paid' if variant=='wrong_checkout' else 'unpaid'})
                subscription_cslive_go.run_subscription(job)
                self.assertEqual(self.payment_api(job)['state'], 'succeeded' if variant=='paid_intent' else 'checking')
                stripe.confirm.assert_called_once()
                if variant!='paid_intent':tail.assert_not_called()

    def test_recovery_restart_and_parallel_reads_settle_only_once(self):
        for module in (subscription, subscription_cslive_go):
            with self.subTest(flow=module.FLOW), self.retry_fixture(module) as job, self.retry_network(module) as (_, stripe, tail):
                stripe.confirm.side_effect = requests.ReadTimeout()
                stripe.session.get.return_value = response(INACTIVE, 400)
                stripe.session.post.return_value = response(INACTIVE, 400)
                module.run_subscription(job)
                self.assertEqual(self.payment_api(job)['state'], 'checking')
                module.run_subscription(job)  # duplicate delivery must not downgrade the pending verification
                self.assertEqual(self.payment_api(job)['state'], 'checking')
                stored = db.scalar('SELECT context_enc FROM subscription_recoveries WHERE order_id=$1', job['id'])
                context = json.loads(decrypt(stored))
                self.assertNotIn('4242424242424242', json.dumps(context))
                self.assertNotIn('cvc', context)
                db.execute("UPDATE subscription_recoveries SET state='running' WHERE order_id=$1", job['id'])
                self.recovery_due(job['id'])
                remote = Mock();remote.get.return_value = response(PAID)
                session = Mock();session.__enter__ = Mock(return_value=remote);session.__exit__ = Mock(return_value=False)
                tail.side_effect = self.finish_recovered
                with patch.object(recovery, 'payment_session', return_value=session):
                    with ThreadPoolExecutor(max_workers=8) as pool:
                        list(pool.map(lambda _: recovery.tick(), range(12)))
                stripe.confirm.assert_called_once()
                remote.get.assert_called_once()
                remote.post.assert_not_called()
                tail.assert_called_once()
                self.assertEqual(self.payment_api(job)['state'], 'succeeded')
                self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='charge'", job['id']), 1)
                self.assertEqual(db.scalar('SELECT state FROM subscription_recoveries WHERE order_id=$1', job['id']), 'completed')
                self.assertEqual(db.scalar('SELECT context_enc FROM subscription_recoveries WHERE order_id=$1', job['id']), '')

    def test_recovery_expiration_and_configured_retry_exhaustion_keep_hold(self):
        for expired in (False, True):
            with self.subTest(expired=expired), self.retry_fixture(subscription_cslive_go, limit=1) as job, self.retry_network(subscription_cslive_go) as (_, stripe, tail):
                stripe.confirm.side_effect = requests.ReadTimeout()
                stripe.session.get.return_value = response({'status':'open'})
                subscription_cslive_go.run_subscription(job)
                self.recovery_due(job['id'])
                if expired:
                    db.execute("UPDATE order_proxy_sessions SET expires_at=now()-interval '1 second' WHERE order_id=$1",job['id'])
                remote=Mock();remote.get.return_value=response({'status':'complete','payment_status':'unpaid'})
                session=Mock();session.__enter__=Mock(return_value=remote);session.__exit__=Mock(return_value=False)
                with patch.object(recovery,'payment_session',return_value=session) as factory:
                    recovery.tick()
                    if expired:factory.assert_not_called()
                self.assertEqual(self.payment_api(job)['state'],'review')
                self.assertEqual(db.scalar('SELECT frozen FROM users WHERE id=$1',job['user_id']),job['fee'])
                self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind IN ('charge','unfreeze')",job['id']),0)
                stripe.confirm.assert_called_once();tail.assert_not_called()

    def test_recovery_paid_tail_retries_transient_only_and_preserves_paid(self):
        from backend import cslive_go_post_payment as followup
        for warning, queued in [('cancellation_not_ready', True), ('network_error', True), ('http_404', False), ('http_403', False)]:
            with self.subTest(warning=warning), self.retry_fixture(subscription_cslive_go) as job, self.retry_network(subscription_cslive_go) as (_, stripe, tail):
                subscription_cslive_go.run_subscription(job)
                cp={'phase':'cancellation_requested','activation':'active','renewal':'on','credentials':{},'cancel_requested':True}
                followup._save(job['id'],cp,warning=warning)
                self.assertEqual(self.payment_api(job)['state'],'succeeded')
                self.assertEqual(db.scalar('SELECT renewal FROM orders WHERE id=$1',job['id']),'retrying' if queued else 'review')
                self.assertEqual(db.scalar('SELECT state FROM subscription_recoveries WHERE order_id=$1',job['id']),'queued' if queued else 'review')
                self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='charge'",job['id']),1)

    def test_recovery_zero_retry_setting(self):
        with self.retry_fixture(subscription_cslive_go,limit=0) as job,self.retry_network(subscription_cslive_go) as (_,stripe,tail):
            stripe.confirm.side_effect=requests.ReadTimeout()
            stripe.session.get.return_value=response({'status':'open'})
            subscription_cslive_go.run_subscription(job)
            self.assertEqual(self.payment_api(job)['state'],'review')
            self.assertEqual(db.scalar('SELECT state FROM subscription_recoveries WHERE order_id=$1',job['id']),'review')
            self.recovery_due(job['id'])
            with patch.object(recovery,'payment_session') as network:
                recovery.tick();network.assert_not_called()
            stripe.confirm.assert_called_once()

    def test_recovery_crash_fence_is_due_before_deadline_and_cannot_race_live_submit(self):
        class ProcessLost(BaseException):
            pass
        for module in (subscription, subscription_cslive_go):
            with self.subTest(flow=module.FLOW), self.retry_fixture(module) as job, self.retry_network(module) as (_, stripe, tail):
                def crash(*args):
                    armed = db.one('SELECT next_at,deadline FROM subscription_recoveries WHERE order_id=$1', job['id'])
                    self.assertLess(armed['next_at'], armed['deadline'])
                    self.recovery_due(job['id'])
                    with patch.object(recovery, 'payment_session') as network:
                        recovery.tick()
                        network.assert_not_called()
                    raise ProcessLost()
                stripe.confirm.side_effect = crash
                with self.assertRaises(ProcessLost): module.run_subscription(job)
                saved = db.one('SELECT state,next_at,deadline FROM subscription_recoveries WHERE order_id=$1', job['id'])
                self.assertEqual(saved['state'], 'armed')
                self.assertLess(saved['next_at'], saved['deadline'])
                remote = Mock(); remote.get.return_value = response(PAID)
                session = Mock(); session.__enter__ = Mock(return_value=remote); session.__exit__ = Mock(return_value=False)
                tail.side_effect = self.finish_recovered
                with patch.object(recovery, 'payment_session', return_value=session): recovery.tick()
                self.assertEqual(self.payment_api(job)['state'], 'succeeded')
                stripe.confirm.assert_called_once()

    def test_recovery_historical_orders_and_paused_workers_do_not_run(self):
        with self.retry_fixture(subscription) as job, self.retry_network(subscription) as (_, stripe, tail):
            subscription_retry.mark_payment_submission(job['id'])
            self.assertEqual(db.scalar('SELECT count(*) FROM subscription_recoveries WHERE order_id=$1', job['id']), 0)
            with patch.object(recovery, 'payment_session') as network:
                recovery.tick(); network.assert_not_called()
            stripe.confirm.assert_not_called()
        with self.retry_fixture(subscription) as job, self.retry_network(subscription) as (_, stripe, tail):
            stripe.confirm.side_effect = requests.ReadTimeout()
            stripe.session.get.return_value = response({'status':'open'})
            subscription.run_subscription(job)
            self.recovery_due(job['id'])
            for mode in ('maintenance', 'paused', 'disabled'):
                def config(key, *args):
                    return {'paused':mode=='paused'} if key=='scheduler' else {'enabled':mode!='disabled'}
                with self.subTest(mode=mode), patch.object(recovery, 'config', side_effect=config), patch('backend.deployment.maintenance_active', return_value=mode=='maintenance'), patch.object(recovery, 'payment_session') as network:
                    recovery.tick(); network.assert_not_called()
            self.assertEqual(db.scalar('SELECT attempts FROM subscription_recoveries WHERE order_id=$1', job['id']), 0)

    def test_recovery_delayed_decline_releases_once_without_confirm_replay(self):
        with self.retry_fixture(subscription) as job, self.retry_network(subscription) as (_, stripe, tail):
            stripe.confirm.side_effect = requests.ReadTimeout()
            stripe.session.get.return_value = response({'status':'open'})
            subscription.run_subscription(job)
            self.recovery_due(job['id'])
            remote = Mock(); remote.get.return_value = response(DECLINED)
            session = Mock(); session.__enter__ = Mock(return_value=remote); session.__exit__ = Mock(return_value=False)
            with patch.object(recovery, 'payment_session', return_value=session):
                recovery.tick(); recovery.tick()
            self.assertEqual(self.payment_api(job)['state'], 'failed')
            self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='unfreeze'", job['id']), 1)
            self.assertEqual(db.scalar('SELECT frozen FROM users WHERE id=$1',job['user_id']),0)
            stripe.confirm.assert_called_once(); tail.assert_not_called()

    def test_recovery_confirm_mismatch_and_conflicting_intents_never_settle(self):
        with self.retry_fixture(subscription) as job, self.retry_network(subscription) as (_, stripe, tail):
            stripe.confirm.return_value = dict(PAID, id='cs_another')
            stripe.session.get.return_value = response({'status':'open'})
            subscription.run_subscription(job)
            self.assertEqual(self.payment_api(job)['state'], 'checking')
            tail.assert_not_called()
        context = {'checkout_id':'cs_original','intent_id':'pi_original','currency':'USD','eid':'old'}
        before = dict(context)
        self.assertFalse(result.remember(context, {'eid':'wrong', 'payment_intent':{'id':'pi_other','status':'succeeded'}}))
        self.assertEqual(context, before)
        self.assertFalse(result.remember(context, {'payment_intent':{'id':'pi_original','currency':'eur','status':'succeeded'}}))

    def test_recovery_corrupt_context_stops_without_releasing_hold(self):
        with self.retry_fixture(subscription) as job, self.retry_network(subscription) as (_, stripe, tail):
            stripe.confirm.side_effect = requests.ReadTimeout()
            stripe.session.get.return_value = response({'status':'open'})
            subscription.run_subscription(job)
            db.execute("UPDATE subscription_recoveries SET context_enc='AAAA',next_at=now() WHERE order_id=$1", job['id'])
            with patch.object(recovery, 'payment_session') as network:
                recovery.tick(); network.assert_not_called()
            self.assertEqual(self.payment_api(job)['state'], 'review')
            self.assertEqual(db.scalar('SELECT frozen FROM users WHERE id=$1',job['user_id']),job['fee'])

    def test_recovery_paid_tail_resumes_without_any_payment_read_or_write(self):
        from backend import cslive_go_post_payment as followup
        with self.retry_fixture(subscription_cslive_go) as job, self.retry_network(subscription_cslive_go) as (_, stripe, tail):
            subscription_cslive_go.run_subscription(job)
            cp={'phase':'cancellation_requested','activation':'active','renewal':'on','credentials':{},'cancel_requested':True}
            followup._save(job['id'], cp, warning='network_error')
            self.recovery_due(job['id'])
            tail.side_effect = self.finish_recovered
            with patch.object(recovery, 'payment_session') as network:
                recovery.tick(); network.assert_not_called()
            stripe.confirm.assert_called_once()
            self.assertEqual(self.payment_api(job)['state'], 'succeeded')
            self.assertEqual(db.scalar('SELECT renewal FROM orders WHERE id=$1',job['id']),'disabled')
            self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='charge'",job['id']),1)

    def test_recovery_confirm_error_body_retains_success_evidence(self):
        for module in (subscription, subscription_cslive_go):
            original = module.StripeHostedClient
            with self.subTest(flow=module.FLOW), self.retry_fixture(module) as job, self.retry_network(module) as (_, stripe, tail):
                stripe.session.post.return_value = response(PAID, 409)
                stripe.confirm.side_effect = lambda cs, form: original._post(stripe, '/v1/payment_pages/'+cs+'/confirm', form, 'confirm')
                module.run_subscription(job)
                stripe.confirm.assert_called_once(); stripe.session.get.assert_not_called()
                self.assertEqual(self.payment_api(job)['state'], 'succeeded')
                self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='charge'",job['id']),1)

    def test_recovery_failed_read_records_redacted_business_error(self):
        from backend import payment_trace
        with self.retry_fixture(subscription) as job:
            remote = Mock(); remote.get.return_value = response(INACTIVE, 400)
            remote.get.return_value.text = json.dumps(INACTIVE)
            context = result.verification_context('cs_test_retry', 'pk_test_fixture')
            with payment_trace.scope(job['id'], 1):
                status, body = result._read(remote, 'get', 'https://api.stripe.com/v1/payment_pages/cs_test_retry', context)
            self.assertEqual(status, 400)
            diagnostics = db.scalar('SELECT diagnostics FROM payment_trace_events WHERE order_id=$1 AND http_status=400 ORDER BY id DESC LIMIT 1',job['id'])
            self.assertEqual(diagnostics['response_error_code'], 'checkout_not_active_session')
