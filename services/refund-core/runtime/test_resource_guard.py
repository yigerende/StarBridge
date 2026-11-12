"""Full fixture transports for seat recovery and optional plan observation."""
import json
import unittest

import refund_runner as runner
from test_refund_transport import ProxyFlow, job


class Occupied(ProxyFlow):
    stale = True
    keep_stale = False
    role = 'member'
    conflict = False

    def reply(self, side, method, url, headers, raw):
        if side == 'mother' and method == 'DELETE' and url.path.endswith('/users/stale-user'):
            if not self.keep_stale: self.stale = False
            return 200, {}, {}
        if self.conflict and url.path.endswith('/workspace/select') and json.loads(raw)['workspace_id'] == self.job['mother']['team_id']:
            self.conflict = False;self.stale = True
            return 409, {'error': {'code': 'workspace_conflict'}}, {}
        status, body, response_headers = super().reply(side, method, url, headers, raw)
        if self.stale and side == 'mother':
            if url.path.endswith('/subscriptions'):
                for row in body['seat_capacity']:row['available'] = 0
            if method == 'GET' and url.path.endswith('/users'):
                body['items'].append({'email':'stale@example.test','id':'stale-user','role':self.role})
        return status, body, response_headers


class ResourceGuardTests(unittest.TestCase):
    def test_full_mother_cleans_residual_before_inviting_and_records_checks(self):
        value=job();events=[]
        with Occupied(value) as upstream:
            def checkpoint(method,*args):
                events.append(method)
                if method=='resource_remove':
                    self.assertFalse(any(m=='DELETE' for _,m,*_ in upstream.records))
            result=runner.execute(value,checkpoint,sleep=lambda _:self.fail('normal flow slept'))
        self.assertTrue(result['ok'],result);self.assertEqual(upstream.errors,[])
        self.assertLess(events.index('resource_remove'),events.index('inviting'))
        self.assertEqual(events.count('mother_check_completed'),1)
        self.assertTrue(all(role=='mother' for role,_,p,*_ in upstream.records if p.endswith(('/subscriptions','/users','/invites')) or '/users/' in p))

    def test_target_team_409_checks_capacity_and_retries_selection_only(self):
        value=job();events=[]
        with Occupied(value) as upstream:
            upstream.stale=False;upstream.conflict=True
            result=runner.execute(value,lambda method,*args:events.append(method),sleep=lambda _:self.fail('no retry delay needed'))
        self.assertTrue(result['ok'],result);self.assertEqual(upstream.errors,[])
        self.assertEqual(events.count('mother_check_started'),2)
        self.assertEqual(sum(p.endswith('/password/verify') for _,_,p,*_ in upstream.records),1)
        self.assertEqual(sum(p.endswith('/mfa/verify') for _,_,p,*_ in upstream.records),1)
        self.assertEqual(sum(p.endswith('/export_and_deactivate') for _,_,p,*_ in upstream.records),1)

    def test_full_owner_or_no_effect_delete_cannot_pass_preflight(self):
        for role,keep in [('account-owner',False),('admin',False),('member',True)]:
            with self.subTest(role=role):
                value=job();events=[]
                with Occupied(value) as upstream:
                    upstream.role=role;upstream.keep_stale=keep
                    result=runner.execute(value,lambda method,*args:events.append(method))
                self.assertFalse(result['ok']);self.assertEqual(result['code'],'mother_capacity')
                self.assertNotIn('inviting',events)
                if role!='member':self.assertFalse(any(m=='DELETE' for _,m,*_ in upstream.records))

    def test_disabled_observation_skips_personal_switch_but_keeps_submission_baseline(self):
        value=job();events=[];value['settings'].update(query_personal_plan=False,recovery={'enabled':True})
        with ProxyFlow(value) as upstream:
            result=runner.execute(value,lambda method,*args:events.append(method))
        self.assertTrue(result['ok'],result);self.assertEqual(upstream.errors,[])
        selected=[json.loads(body)['workspace_id'] for _,_,path,_,body in upstream.records if path.endswith('/workspace/select')]
        self.assertEqual(selected,['fixture-team']);self.assertIn('personal_baseline',events)
        self.assertFalse(any(p.endswith('/me') for _,_,p,*_ in upstream.records))
        self.assertEqual(sum(p.endswith('/export_and_deactivate') for _,_,p,*_ in upstream.records),1)

    def test_missing_capacity_cannot_authorize_invite(self):
        class Incomplete(ProxyFlow):
            def reply(self,*args):
                status,body,headers=super().reply(*args)
                if args[2].path.endswith('/subscriptions'):body={}
                return status,body,headers
        value=job();events=[]
        with Incomplete(value) as upstream:result=runner.execute(value,lambda method,*args:events.append(method))
        self.assertEqual(result['code'],'mother_preflight_failed');self.assertNotIn('inviting',events)


if __name__=='__main__':unittest.main()
