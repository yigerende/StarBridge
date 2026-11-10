import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import diagnostics
import refund_runner as runner
import test_refund_runner
from test_refund_transport import ProxyFlow


class SearchMissUpstream(ProxyFlow):
    """The search index omits a member who still exists in the actual roster."""
    keep_member = False

    def reply(self, role, method, url, headers, raw):
        if role == 'mother' and method == 'GET' and url.path.endswith(('/users', '/invites')):
            rows = []
            if url.path.endswith('/users') and self.members and 'query' not in parse_qs(url.query):
                rows = [{'email': self.job['account']['email'], 'id': 'child-user', 'role': 'member'}]
            return 200, {'items': rows, 'total': len(rows)}, {}
        if self.keep_member and role == 'mother' and method == 'DELETE':
            return 200, {}, {}
        return super().reply(role, method, url, headers, raw)


class MemberCompletionTests(unittest.TestCase):
    def test_null_email_member_is_removed_and_verified_over_real_transport(self):
        class NullEmailUpstream(SearchMissUpstream):
            def reply(self, *args):
                status, data, headers = super().reply(*args)
                if isinstance(data, dict):
                    for row in data.get('items', []):
                        row['email'] = None
                return status, data, headers
        for keep_member in (False, True):
            with self.subTest(delete_takes_effect=not keep_member):
                job = test_refund_runner.RunnerTests().job('accepted')
                job['settings']['recovery'] = {'enabled': True}
                with NullEmailUpstream(job) as upstream:
                    upstream.members = True
                    upstream.keep_member = keep_member
                    events = []
                    result = runner.execute(job, lambda method, *args: events.append(method))
                    self.assertEqual(result['ok'], not keep_member, result)
                    self.assertEqual(sum(method == 'DELETE' for _, method, *_ in upstream.records), 1)
                    self.assertEqual('completed' in events, not keep_member)

    def test_null_email_rows_use_only_verified_identity(self):
        with patch.object(runner.requests, 'Session'):
            client = runner.MotherClient(test_refund_runner.RunnerTests().job('accepted'))
        # An unrelated anonymized member must not hide the target or prevent
        # the subsequent full-roster absence check.
        other = {'email': None, 'id': 'other-user', 'role': 'member'}
        target = {'email': None, 'id': 'child-user', 'role': 'standard-user'}
        state = {'present': True}
        def call(method, suffix, body=None):
            if method == 'DELETE':
                self.assertEqual(suffix, '/users/child-user')
                state['present'] = False
                return Mock()
            rows = [other] + ([target] if state['present'] else []) if suffix.startswith('/users?') else []
            return Mock(json=Mock(return_value={'items': rows, 'total': len(rows)}))
        client.call = Mock(side_effect=call)
        evidence = []
        with diagnostics.capture(lambda _, params: evidence.append(params)):
            client.remove('child-user')
            client.confirm_removed()
        self.assertFalse(state['present'])
        payload = json.dumps(evidence)
        self.assertIn('user_id', payload)
        self.assertNotIn('child-user', payload)
        self.assertNotIn('other-user', payload)

    def test_missing_identity_cannot_prove_absence_or_authorize_removal(self):
        for collection, child, row in [
            ('users', '', {'email': None, 'id': 'child-user', 'role': 'member'}),
            ('users', 'child-user', {'email': None, 'role': 'member'}),
            ('users', 'child-user', {'email': None, 'id': 123, 'role': 'member'}),
            ('users', 'child-user', {'email': {'private': 'value'}, 'id': 'child-user'}),
            ('invites', 'child-user', {'email': None, 'id': 'child-user', 'role': 'member'}),
        ]:
            with self.subTest(collection=collection, child=child, row=row):
                job = test_refund_runner.RunnerTests().job('accepted')
                job['child_user_id'] = child
                with patch.object(runner.requests, 'Session'):
                    client = runner.MotherClient(job)
                client.call = Mock(return_value=Mock(json=Mock(return_value={'items': [row], 'total': 1})))
                evidence = []
                with diagnostics.capture(lambda _, params: evidence.append(params)), self.assertRaises(runner.UpstreamFailure) as result:
                    client.find(collection)
                self.assertEqual(result.exception.code, 'mother_shape')
                self.assertIn('member_identity_missing', json.dumps(evidence))
                self.assertNotIn('private', json.dumps(evidence))
                self.assertEqual(client.call.call_args.args[0], 'GET')

    def test_null_email_id_match_does_not_allow_removing_owner(self):
        with patch.object(runner.requests, 'Session'):
            client = runner.MotherClient(test_refund_runner.RunnerTests().job('accepted'))
        client.call = Mock(return_value=Mock(json=Mock(return_value={
            'items': [{'email': None, 'id': 'child-user', 'role': 'account-owner'}], 'total': 1})))
        with self.assertRaises(runner.UpstreamFailure) as result:
            client.remove('child-user')
        self.assertEqual(result.exception.code, 'member_identity')
        self.assertEqual(client.call.call_count, 1)

    def test_search_miss_cannot_skip_actual_member_removal(self):
        job = test_refund_runner.RunnerTests().job('accepted')
        job['settings']['recovery'] = {'enabled': True}
        with SearchMissUpstream(job) as upstream:
            upstream.members = True
            events = []
            result = runner.execute(job, lambda method, *args: events.append(method))
            self.assertTrue(result['ok'], result)
            self.assertFalse(upstream.members, 'reported completed while member still occupies seat')
            self.assertEqual(sum(method == 'DELETE' for _, method, *_ in upstream.records), 1)
            self.assertIn('completed', events)

    def test_successful_delete_response_does_not_prove_member_absence(self):
        job = test_refund_runner.RunnerTests().job('accepted')
        job['settings']['recovery'] = {'enabled': True}
        with SearchMissUpstream(job) as upstream:
            upstream.members = True
            upstream.keep_member = True
            events = []
            result = runner.execute(job, lambda method, *args: events.append(method))
            self.assertFalse(result['ok'])
            self.assertEqual(result['code'], 'cleanup_pending')
            self.assertNotIn('completed', events)

    def test_confirmation_is_required_even_with_recovery_disabled(self):
        mother = Mock()
        mother.confirm_removed.side_effect = runner.UpstreamFailure('cleanup_pending')
        events = []
        result = runner.execute(test_refund_runner.RunnerTests().job('accepted'), lambda method, *args: events.append(method), lambda _: mother)
        self.assertFalse(result['ok'])
        self.assertNotIn('completed', events)

    def test_known_child_id_matches_changed_display_email_and_logs_no_identity(self):
        with patch.object(runner.requests, 'Session'):
            client = runner.MotherClient(test_refund_runner.RunnerTests().job('accepted'))
        state = {'present': True}
        def call(method, suffix, body=None):
            if method == 'DELETE':
                self.assertEqual(suffix, '/users/child-user')
                state['present'] = False
                return Mock()
            self.assertEqual(parse_qs(urlsplit(suffix).query, keep_blank_values=True)['query'], [''])
            rows = [{'email': 'changed-private@example.com', 'id': 'child-user', 'role': 'member'}] if state['present'] and suffix.startswith('/users?') else []
            return Mock(json=Mock(return_value={'items': rows, 'total': len(rows)}))
        client.call = Mock(side_effect=call)
        evidence = []
        with diagnostics.capture(lambda _, params: evidence.append(params)):
            client.remove('child-user')
            client.confirm_removed()
        self.assertFalse(state['present'])
        payload = json.dumps(evidence)
        self.assertNotIn('changed-private', payload)
        self.assertNotIn('child-user', payload)
        self.assertIn('membership', payload)
        self.assertIn('user_id', payload)

    def test_repeated_page_is_not_a_complete_roster(self):
        with patch.object(runner.requests, 'Session'):
            client = runner.MotherClient(test_refund_runner.RunnerTests().job('accepted'))
        client.call = Mock(return_value=Mock(json=Mock(return_value={
            'items': [{'email': 'other@example.com', 'id': 'other-user', 'role': 'member'}], 'total': 3})))
        with self.assertRaises(runner.UpstreamFailure) as result:
            client.confirm_removed()
        self.assertEqual(result.exception.code, 'mother_pagination')
        self.assertEqual(client.call.call_count, 2)

    def test_full_page_without_total_continues_with_spaces_page_size(self):
        with patch.object(runner.requests, 'Session'):
            client = runner.MotherClient(test_refund_runner.RunnerTests().job('accepted'))
        offsets = []
        def call(method, suffix, body=None):
            if method == 'DELETE':
                self.assertEqual(suffix, '/users/child-user')
                return Mock()
            query = parse_qs(urlsplit(suffix).query, keep_blank_values=True)
            self.assertEqual(query['limit'], ['25'])
            self.assertEqual(query['query'], [''])
            offset = int(query['offset'][0]); offsets.append(offset)
            rows = ([{'email': f'other{i}@example.com', 'id': f'other-{i}'} for i in range(25)]
                    if offset == 0 else [{'email': 'child@example.com', 'id': 'child-user', 'role': 'member'}])
            return Mock(json=Mock(return_value={'items': rows}))
        client.call = Mock(side_effect=call)
        client.remove('child-user')
        self.assertEqual(offsets, [0, 25])


if __name__ == '__main__':
    unittest.main()
