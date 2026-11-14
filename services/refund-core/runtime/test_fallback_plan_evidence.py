"""Offline timing, failure evidence, and credential isolation contracts."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import diagnostics
import fallback_plan_evidence as evidence
import refund_plan_fallback as fallback
from test_merge_v2 import session
from test_refund_plan_fallback import FallbackTests


class PlanEvidenceTests(unittest.TestCase):
    def test_every_fallback_login_waits_before_first_plan_request(self):
        for configured, expected in ((None, 5), (0, 0), (7, 7), (60, 60), (-1, 5), (True, 5)):
            job = FallbackTests().job()
            if configured is None: job['settings']['resource_recovery'] = {}
            else: job['settings']['resource_recovery']['fallback_plan_wait_seconds'] = configured
            events, traces = [], []
            flow = Mock()
            flow.login.side_effect = lambda: (events.append('login') or session('personal', job['account']['email']))
            def query(*_):
                events.append('query')
                return 'free'
            with patch.object(fallback.time, 'sleep', side_effect=lambda n: events.append(('wait', n))), \
                    patch.object(fallback, 'check_plan', side_effect=query), \
                    diagnostics.capture(lambda _, p: traces.append(p['diagnostic'])):
                for phase in ('', 'checking'):
                    job['fallback_phase'] = phase
                    result = fallback.execute(job, lambda *_: None, Mock(), lambda *_: flow)
                    self.assertTrue(result['ok'])
            expected_events = ['login'] + ([('wait', expected)] if expected else []) + ['query']
            self.assertEqual(events, expected_events * 2)
            self.assertEqual([t['evidence']['plan_check']['wait_seconds'] for t in traces
                              if t['evidence'].get('plan_check', {}).get('phase') == 'wait'], [expected] * 2)
            flow.submit_export.assert_not_called()

    def test_login_or_identity_failure_never_waits_or_queries(self):
        job = FallbackTests().job()
        for result in (RuntimeError('login failed'), session('personal', 'wrong@test.invalid')):
            flow = Mock()
            if isinstance(result, Exception): flow.login.side_effect = result
            else: flow.login.return_value = result
            with patch.object(fallback.time, 'sleep') as sleep, patch.object(fallback, 'check_plan') as query:
                self.assertFalse(fallback.execute(job, lambda *_: None, Mock(), lambda *_: flow)['ok'])
            sleep.assert_not_called(); query.assert_not_called()

    def test_mismatch_records_actual_selected_default_and_missing_types(self):
        job = FallbackTests().job(); sess = session('personal', job['account']['email'])
        sess['account']['structure'] = 'personal'; sess['account']['planType'] = 'free'
        response = {'accounts': {'default': {'account': {'account_id': 'different', 'structure': 'workspace',
                    'plan_type': 'team', 'private': 'RAW_SECRET'}, 'entitlement': {'has_active_subscription': False}}}}
        flow = Mock(); flow.device_id = 'fixture'
        flow.web_session.get.side_effect = [Mock(status_code=200, json=Mock(return_value={'email':job['account']['email']})),
                                           Mock(status_code=200, json=Mock(return_value=response))]
        traces = []
        with diagnostics.capture(lambda _, p: traces.append(p['diagnostic'])):
            with self.assertRaisesRegex(ValueError, 'not the authenticated personal account'):
                fallback.check_plan(flow, sess, job)
        d = [x for x in traces if x['evidence'].get('plan_check')][-1]
        p = d['evidence']['plan_check']
        self.assertEqual(p['reason'], 'personal_account_mismatch')
        self.assertEqual(p['fields']['token.account_id'], 'personal')
        self.assertEqual(p['fields']['account.account_id'], 'different')
        self.assertEqual(p['fields']['account.structure'], 'workspace')
        self.assertEqual(p['fields']['selected_key'], 'default')
        self.assertEqual(p['types']['entitlement.subscription_plan'], 'missing')
        self.assertIs(p['checks']['entitlement.has_active_subscription'], False)
        self.assertIs(p['checks']['account_id_matches'], False)
        self.assertIs(p['checks']['personal_structure'], False)
        self.assertEqual(d['http_status'], 200)
        raw = json.dumps(traces)
        for secret in ('RAW_SECRET', sess['accessToken'], job['account']['email'], job['account']['password']):
            self.assertNotIn(secret, raw)

    def test_null_invalid_unknown_and_free_are_distinguishable(self):
        job = FallbackTests().job(); sess = session('personal', job['account']['email'])
        data = {'accounts': {'personal': {'account': {'account_id':'personal','structure':None,'plan_type':['secret']},
                  'entitlement': {'subscription_plan':'chatgptpromax','has_active_subscription':False}}}}
        p = evidence.snapshot(sess, job, data)
        self.assertEqual(p['types']['account.structure'], 'null')
        self.assertEqual(p['types']['account.plan_type'], 'array')
        self.assertNotIn('account.plan_type', p['fields'])
        self.assertEqual(p['fields']['entitlement.subscription_plan'], 'chatgptpromax')
        data['accounts']['personal']['account'].update(structure='personal', plan_type='free')
        flow = Mock(); flow.device_id='fixture'
        flow.web_session.get.side_effect=[Mock(status_code=200,json=Mock(return_value={'email':job['account']['email']})),Mock(status_code=200,json=Mock(return_value=data))]
        traces=[]
        with diagnostics.capture(lambda _,p:traces.append(p['diagnostic'])):
            self.assertEqual(fallback.check_plan(flow,sess,job),'free')
        self.assertEqual(traces[-1]['evidence']['plan_check']['result'],'free')

    def test_bounded_redaction_reserved_budget_and_telemetry_failure(self):
        job=FallbackTests().job();sess=session('personal',job['account']['email'])
        data={'accounts':{str(i):{'account':{}} for i in range(50)}}
        data['accounts']['default']={'account':{'account_id':'https://secret.example/?token=secret','structure':'secret@example.test'}}
        p=evidence.snapshot(sess,job,data)
        self.assertEqual(len(p['account_keys']),16);self.assertTrue(p['truncated'])
        self.assertNotIn('secret',json.dumps(p))
        traces=[]
        with diagnostics.capture(lambda _,p:traces.append(p['diagnostic'])):
            for _ in range(130):diagnostics.record({'stage':'login'})
            evidence.record(sess,job,phase='accounts',data=data)
        self.assertIn('plan_check',traces[-1]['evidence'])
        with patch.object(diagnostics,'record',side_effect=RuntimeError('sink down')):
            evidence.record(sess,job,phase='accounts',data=data)

if __name__ == '__main__':unittest.main()
