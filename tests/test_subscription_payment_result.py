"""Submitted-payment recovery with synthetic upstreams and an isolated database."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient

from backend import db, subscription, subscription_cslive_go
from backend.common import digest, uid
from backend.subscription_payment_result import classify

FLOWS = (subscription, subscription_cslive_go)
PAID = {'status': 'complete', 'payment_status': 'paid',
        'payment_intent': {'id': 'pi_fixture', 'status': 'succeeded', 'amount_received': 1234}}
DECLINED = {'payment_status': 'unpaid', 'payment_intent': {
    'id': 'pi_fixture', 'status': 'requires_payment_method',
    'last_payment_error': {'code': 'card_declined', 'decline_code': 'insufficient_funds'}}}


def response(data=None, status=200):
    return Mock(status_code=status, headers={'content-type': 'application/json'},
                text='{}', json=Mock(return_value=data))


class SubscriptionPaymentResultTests:
    def payment_api(self, job):
        key = 'sb_test_' + uid()
        db.execute('INSERT INTO api_keys(id,user_id,name,hash,prefix) VALUES($1,$2,$3,$4,$5)',
                   uid(), job['user_id'], 'payment-result-test', digest(key), key[:12])
        client = TestClient(self.client.app, headers={'Authorization': 'Bearer ' + key})
        try:
            return self.ok(client.get('/api/v1/orders/' + job['id']))['payment']
        finally:
            client.close()

    def test_payment_error_reads_original_transaction_and_settles_once(self):
        for module in FLOWS:
            original_client = module.StripeHostedClient
            for problem in ('409', 'timeout', 'invalid_json'):
                with self.subTest(flow=module.FLOW, problem=problem), self.retry_fixture(module) as job, self.retry_network(module) as (generate, stripe, followup):
                    if problem == '409':
                        # Exercise the actual HTTP non-200 exception, not just a synthetic code.
                        stripe.session.post.return_value = response(status=409)
                        stripe.confirm.side_effect = lambda cs, form: original_client._post(
                            stripe, '/v1/payment_pages/' + cs + '/confirm', form, 'confirm')
                    else:
                        stripe.confirm.side_effect = (requests.ReadTimeout('response lost') if problem == 'timeout'
                                                      else RuntimeError('confirm 返回非 JSON'))
                    stripe.session.get.side_effect = [RuntimeError('read temporarily unavailable'),
                                                      response(status=503), response(['malformed']), response(PAID)]
                    before = db.one('SELECT balance,frozen FROM users WHERE id=$1', job['user_id'])
                    module.run_subscription(job)
                    self.assertEqual(db.scalar('SELECT status FROM orders WHERE id=$1', job['id']), 'succeeded')
                    self.assertEqual(stripe.confirm.call_count, 1)
                    self.assertEqual(stripe.session.get.call_count, 4)
                    self.assertTrue(all(call.args[0].endswith('/cs_test_retry') for call in stripe.session.get.call_args_list))
                    self.assertEqual(generate.call_count, 1)
                    self.assertEqual(followup.call_count, 1)
                    self.assertEqual(followup.call_args.kwargs['responses'][0], PAID)
                    self.assertEqual(self.payment_api(job)['state'], 'succeeded')
                    self.assertEqual(self.payment_api(job)['fee_state'], 'charged')
                    after = db.one('SELECT balance,frozen FROM users WHERE id=$1', job['user_id'])
                    self.assertEqual(after, {'balance': before['balance'] - job['fee'], 'frozen': 0})
                    self.assertEqual(db.scalar('SELECT payment_amount FROM subscription_runs WHERE order_id=$1', job['id']), 1234)
                    # Duplicate workers and a late failure cannot double-charge or release a settled fee.
                    module.run_subscription(job)
                    self.assertFalse(module._succeed_order(job['id'], job['user_id'], job['fee'], '4242', 1234, 'USD'))
                    module._fail_order(job['id'], job['user_id'], job['fee'], 'late failure')
                    self.assertEqual(stripe.confirm.call_count, 1)
                    self.assertEqual(followup.call_count, 1)
                    self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='charge'", job['id']), 1)
                    self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='unfreeze'", job['id']), 0)

    def test_payment_unknown_preserves_hold_and_blocks_repayment(self):
        for module in FLOWS:
            with self.subTest(flow=module.FLOW), self.retry_fixture(module) as job, self.retry_network(module) as (_, stripe, followup):
                stripe.confirm.side_effect = RuntimeError('confirm failed [409]')
                stripe.session.get.side_effect = [response(status=409), requests.ReadTimeout(),
                    response({'status': 'complete', 'payment_status': 'unpaid'}), response(None), response({'status': 'open'})]
                module.run_subscription(job)
                self.assertEqual(stripe.session.get.call_count, 5)
                self.assertEqual(db.scalar('SELECT status FROM orders WHERE id=$1', job['id']), 'processing')
                self.assertEqual(db.scalar('SELECT frozen FROM users WHERE id=$1', job['user_id']), job['fee'])
                self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind IN ('charge','unfreeze')", job['id']), 0)
                self.assertEqual(self.payment_api(job)['state'], 'checking')
                self.assertEqual(self.payment_api(job)['fee_state'], 'held')
                module.run_subscription(job)
                db.execute("UPDATE orders SET status='queued' WHERE id=$1", job['id'])
                self.assertIsNone(module.claim_subscription())
                self.assertEqual(stripe.confirm.call_count, 1)
                followup.assert_not_called()

    def test_payment_verified_decline_releases_once(self):
        for module in FLOWS:
            with self.subTest(flow=module.FLOW), self.retry_fixture(module) as job, self.retry_network(module) as (_, stripe, followup):
                stripe.confirm.side_effect = RuntimeError('confirm failed [409]')
                stripe.session.get.return_value = response(DECLINED)
                module.run_subscription(job)
                module._fail_order(job['id'], job['user_id'], job['fee'], 'duplicate failure')
                self.assertEqual(db.scalar('SELECT status FROM orders WHERE id=$1', job['id']), 'failed')
                self.assertEqual(db.scalar('SELECT frozen FROM users WHERE id=$1', job['user_id']), 0)
                self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='unfreeze'", job['id']), 1)
                self.assertEqual(stripe.confirm.call_count, 1)
                followup.assert_not_called()

    def test_payment_approval_error_still_checks_result(self):
        for module in FLOWS:
            for result, status in ((PAID, 'succeeded'), ({'status': 'open'}, 'processing')):
                with self.subTest(flow=module.FLOW, status=status), self.retry_fixture(module) as job, self.retry_network(module) as (_, stripe, followup), patch.object(module, 'payment_session') as session:
                    stripe.confirm.return_value = {'submission_attempt': {'state': 'requires_approval'}}
                    session.return_value.post.return_value = response({'result': 'blocked'})
                    stripe.session.get.return_value = response(result)
                    module.run_subscription(job)
                    self.assertEqual(db.scalar('SELECT status FROM orders WHERE id=$1', job['id']), status)
                    self.assertEqual(stripe.confirm.call_count, 1)
                    self.assertEqual(session.return_value.post.call_count, 1)
                    self.assertEqual(followup.call_count, int(status == 'succeeded'))

    def test_payment_success_needs_no_poll_and_followup_failure_keeps_charge(self):
        for module in FLOWS:
            with self.subTest(flow=module.FLOW), self.retry_fixture(module) as job, self.retry_network(module) as (_, stripe, followup):
                followup.side_effect = RuntimeError('post-payment unavailable')
                module.run_subscription(job)
                stripe.session.get.assert_not_called()
                self.assertEqual(self.payment_api(job)['state'], 'succeeded')
                self.assertEqual(db.scalar('SELECT activation FROM orders WHERE id=$1', job['id']), 'review')
                self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='charge'", job['id']), 1)

    def test_payment_concurrent_settlement_charges_exactly_once(self):
        for module in FLOWS:
            with self.subTest(flow=module.FLOW), self.retry_fixture(module) as job:
                with ThreadPoolExecutor(max_workers=12) as pool:
                    results = list(pool.map(lambda _: module._succeed_order(job['id'], job['user_id'], job['fee'], '4242', 1234, 'USD'), range(24)))
                self.assertEqual(sum(results), 1)
                self.assertEqual(db.scalar("SELECT count(*) FROM ledger WHERE order_id=$1 AND kind='charge'", job['id']), 1)
                self.assertEqual(db.scalar('SELECT balance FROM users WHERE id=$1', job['user_id']), 1000000 - job['fee'])

    def test_payment_classifier_requires_financial_evidence(self):
        for body in (None, [], {'status': 'complete'}, {'status': 'failed'},
                     {'submission_attempt': {'state': 'failed'}}, {'payment_intent': 'pi_fixture'},
                     {'payment_intent': {'status': 'processing', 'last_payment_error': {'code': 'card_declined'}}}):
            self.assertEqual(classify(body), (False, '', 0))
        self.assertEqual(classify(PAID), (True, '', 1234))
        self.assertEqual(classify(DECLINED), (False, 'insufficient_funds', 0))
