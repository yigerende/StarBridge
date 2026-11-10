import sys
import unittest
from urllib.parse import parse_qs, urlsplit
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import refund_runner as runner
from manager_oauth import bridge


class RunnerTests(unittest.TestCase):
    def test_only_invite_post_401_has_invitation_authorization_code(self):
        for method, path, status, expected, operation in [
            ('POST', '/invites', 401, 'mother_invite_auth', 'invite_send'),
            ('GET', '/invites?offset=0', 401, 'mother_http', 'invites_read'),
            ('POST', '/invites', 403, 'mother_http', 'invite_send'),
            ('DELETE', '/users/child', 401, 'mother_http', 'member_remove'),
        ]:
            with self.subTest(method=method, status=status), patch.object(runner.requests, 'Session'):
                c=runner.MotherClient(self.job())
                c.session.request.return_value=Mock(status_code=status, headers={}, text='{}', json=lambda: {})
                with self.assertRaises(runner.UpstreamFailure) as failure:
                    c.call(method,path)
                self.assertEqual(failure.exception.code,expected)
                self.assertEqual(failure.exception.operation,operation)
        mother=Mock();login=Mock();steps=[]
        mother.invite.side_effect=runner.UpstreamFailure('mother_invite_auth',401,operation='invite_send')
        result=runner.execute(self.job(),lambda method,*a:steps.append(method),lambda _:mother,login)
        self.assertEqual(result['code'],'mother_invite_auth')
        self.assertEqual(result['evidence']['stage'],'invite_send')
        self.assertEqual(result['evidence']['side'],'mother')
        self.assertEqual([s for s in steps if s!='diagnostic'],['inviting'])
        login.assert_not_called()

    def test_preflight_failure_never_invites_or_logs_in(self):
        mother=Mock();login=Mock();steps=[]
        mother.preflight.side_effect=runner.UpstreamFailure('mother_preflight_auth',401)
        result=runner.execute(self.job(),lambda method,*a:steps.append(method),lambda _:mother,login)
        self.assertEqual(result['code'],'mother_preflight_auth')
        self.assertEqual(result['http_status'],401)
        self.assertEqual([s for s in steps if s != 'diagnostic'],[])
        mother.invite.assert_not_called();mother.remove.assert_not_called();login.assert_not_called()

    def test_preflight_is_read_only_and_only_initial(self):
        with patch.object(runner.requests,'Session'):
            c=runner.MotherClient(self.job());c.call=Mock(side_effect=runner.UpstreamFailure('mother_http',401))
            with self.assertRaises(runner.UpstreamFailure) as result:c.preflight()
            self.assertEqual(result.exception.code,'mother_preflight_auth')
            self.assertEqual(c.call.call_args.args[0],'GET')
        mother=Mock();mother.invite.side_effect=runner.UpstreamFailure('mother_http',401)
        result=runner.execute(self.job('inviting'),lambda *a:None,lambda _:mother)
        self.assertEqual(result['code'],'mother_http')
        mother.preflight.assert_not_called()

    def job(self, step="assigned"):
        return {"id":"fixture", "step":step, "child_user_id":"child-user",
                "account":{"email":"child@example.com", "password":"private-password", "totp_secret":"JBSWY3DPEHPK3PXP"},
                "mother":{"email":"mother@example.com", "team_id":"target-team", "access_token":"private-token", "seat_type":"default"},
                "mother_proxy":"http://mother.invalid:8080", "child_proxy":"http://child.invalid:8081", "settings":{"max_attempts":2,"retry_seconds":5}}

    def test_full_flow_and_accepted_restart(self):
        methods=[];mother=Mock();login=Mock()
        def export(_):
            bridge.call("merge_v2_ready",team_id="target-team",user_id="child-user")
            bridge.call("merge_v2_submitting",team_id="target-team")
            bridge.call("merge_v2_accepted")
            return {"success":True}
        login.submit_export.side_effect=export
        result=runner.execute(self.job(),lambda method,*args: methods.append(method),lambda j:mother,lambda *args:login)
        self.assertTrue(result["ok"])
        self.assertEqual(methods,["inviting","invited","login","merge_v2_ready","merge_v2_submitting","merge_v2_accepted","removing","completed"])
        mother.invite.assert_called_once();mother.remove.assert_called_once_with("child-user")
        factory=Mock(side_effect=AssertionError("must not log in again"));mother=Mock();methods=[]
        self.assertTrue(runner.execute(self.job("accepted"),lambda method,*args:methods.append(method),lambda j:mother,factory)["ok"])
        mother.invite.assert_not_called();factory.assert_not_called();self.assertEqual(methods,["removing","completed"])

    def test_unknown_never_removes_or_retries(self):
        mother=Mock();login=Mock();login.submit_export.return_value={"success":False,"retryable":False,"error_code":"export_uncertain"}
        result=runner.execute(self.job(),lambda *args:None,lambda j:mother,lambda *args:login)
        self.assertFalse(result["ok"]);login.login.assert_called_once();mother.remove.assert_not_called()
        factory=Mock();self.assertFalse(runner.execute(self.job("submitting"),mother_factory=factory)["ok"]);factory.assert_not_called()

    def test_rejected_workspace_relogs_and_has_bounded_retry(self):
        mother=Mock();login=Mock();login.submit_export.return_value={"success":False,"retryable":True,"error_code":"export_rejected"};sleep=Mock()
        result=runner.execute(self.job(),lambda *args:None,lambda j:mother,lambda *args:login,sleep)
        self.assertFalse(result["ok"]);self.assertEqual(login.login.call_count,2);sleep.assert_called_once_with(5);mother.remove.assert_not_called()

    def test_checkpoint_failure_stops_before_side_effect(self):
        mother=Mock()
        def checkpoint(*args):raise runner.StopProtocol()
        self.assertFalse(runner.execute(self.job(),checkpoint,lambda j:mother)["ok"])
        mother.invite.assert_not_called();mother.remove.assert_not_called()

    def test_extra_verification_does_not_retry_or_leak(self):
        mother=Mock();login=Mock();login.login.side_effect=runner.NeedsVerification("private-password")
        result=runner.execute(self.job(),lambda *args:None,lambda j:mother,lambda *args:login)
        self.assertFalse(result['ok']);self.assertEqual(result['code'],'extra_verification');self.assertNotIn('private-password',str(result));login.login.assert_called_once();mother.remove.assert_not_called()

    def test_mother_targets_only_exact_child_and_no_blanket_cleanup(self):
        with patch.object(runner.requests,"Session") as factory:
            c=runner.MotherClient(self.job())
            c.find=Mock(return_value={"id":"child-user","email":"child@example.com","role":"standard-user"})
            c.call=Mock();c.remove("child-user")
            c.call.assert_called_once_with("DELETE","/users/child-user")
            c.call.reset_mock();c.find.return_value={"id":"other-user","role":"standard-user"}
            with self.assertRaises(runner.UpstreamFailure):c.remove("child-user")
            c.call.assert_not_called()
            c.find.return_value={"id":"child-user","role":"account-owner"}
            with self.assertRaises(runner.UpstreamFailure):c.remove("child-user")
            c.call.assert_not_called()
            self.assertEqual(factory.call_args.kwargs["proxies"],{"http":"http://mother.invalid:8080","https":"http://mother.invalid:8080"})

    def test_absence_requires_readable_complete_member_list(self):
        with patch.object(runner.requests,"Session"):
            c=runner.MotherClient(self.job());c.call=Mock(return_value=Mock(json=Mock(return_value={"error":"unexpected"})))
            with self.assertRaises(runner.UpstreamFailure):c.remove("child-user")
            c.call.return_value.json.return_value={"items":[]}
            c.remove("child-user")
            self.assertTrue(all(call.args[0]=="GET" for call in c.call.call_args_list))

    def test_removal_error_cannot_report_complete(self):
        mother=Mock();mother.remove.side_effect=runner.UpstreamFailure("mother_http",401);steps=[]
        result=runner.execute(self.job("accepted"),lambda method,*args:steps.append(method),lambda j:mother)
        self.assertFalse(result["ok"]);self.assertEqual([s for s in steps if s != 'diagnostic'],["removing"])

    def test_partial_member_pages_do_not_skip_target(self):
        with patch.object(runner.requests,"Session"):
            client=runner.MotherClient(self.job()); offsets=[]
            def call(method, suffix, body=None):
                if method == "DELETE": return Mock()
                offset=int(parse_qs(urlsplit(suffix).query)["offset"][0]); offsets.append(offset)
                if offset == 0:
                    data={"items":[{"email":"other@example.com"}], "has_more":True, "total":2}
                elif offset == 1:
                    data={"items":[{"email":"child@example.com","id":"child-user","role":"member"}], "has_more":False, "total":2}
                else:
                    data={"items":[], "has_more":False, "total":2}
                return Mock(json=Mock(return_value=data))
            client.call=Mock(side_effect=call)
            client.remove("child-user")
            self.assertEqual(offsets,[0,1])
            self.assertEqual(client.call.call_args.args,("DELETE","/users/child-user"))

    def test_malformed_pagination_never_confirms_removal(self):
        for data in [{"items":[],"total":"2"},{"items":[],"total":True},
                     {"items":[],"total":-1},{"items":[],"has_more":"true"},
                     {"items":[],"has_more":False,"total":2},
                     {"items":[],"has_more":True}]:
            with self.subTest(data=data),patch.object(runner.requests,"Session"):
                client=runner.MotherClient(self.job())
                client.call=Mock(return_value=Mock(json=Mock(return_value=data)))
                with self.assertRaises(runner.UpstreamFailure):client.remove("child-user")
                self.assertTrue(all(c.args[0]=="GET" for c in client.call.call_args_list))

    def test_child_login_and_export_keep_global_proxy_across_retries(self):
        job=self.job();mother=Mock();payloads=[]
        def login_factory(_id,payload):
            payloads.append(dict(payload))
            flow=Mock()
            if len(payloads)==1:
                flow.submit_export.return_value={"success":False,"retryable":True,"error_code":"export_rejected"}
            else:
                def exported(_):
                    bridge.call("merge_v2_ready",team_id="target-team",user_id="child-user")
                    bridge.call("merge_v2_accepted")
                    return {"success":True}
                flow.submit_export.side_effect=exported
            return flow
        result=runner.execute(job,lambda *args:None,lambda _:mother,login_factory,lambda _:None)
        self.assertTrue(result["ok"])
        self.assertEqual(len(payloads),2)
        self.assertTrue(all(p["proxy"]=="http://child.invalid:8081" for p in payloads))
        self.assertNotEqual(job["mother_proxy"],payloads[0]["proxy"])

    def test_missing_proxy_never_falls_back_or_calls_upstream(self):
        for key in ["child_proxy","mother_proxy"]:
            job=self.job();job[key]="";factory=Mock()
            result=runner.execute(job,mother_factory=factory)
            self.assertEqual(result,{"ok":False,"code":"proxy_missing"})
            factory.assert_not_called()
        job=self.job("accepted");job["child_proxy"]=""
        self.assertTrue(runner.execute(job,lambda *args:None,lambda _:Mock())["ok"])

    def test_mother_probe_is_read_only_and_uses_dedicated_proxy(self):
        with patch.object(runner.requests,"Session") as factory:
            response=Mock(status_code=200,headers={},json=Mock(return_value={"items":[]}))
            factory.return_value.request.return_value=response
            result=runner.probe_mother(self.job())
            self.assertTrue(result["valid"])
            self.assertEqual(factory.call_args.kwargs["proxies"],{"http":"http://mother.invalid:8080","https":"http://mother.invalid:8080"})
            self.assertEqual(factory.return_value.request.call_args.args[0],"GET")
            self.assertEqual(factory.return_value.request.call_count,2)
            self.assertTrue(all(c.args[0]=="GET" for c in factory.return_value.request.call_args_list))
            response.status_code=401
            response.json.return_value={"access_token":"private-secret"}
            result=runner.probe_mother(self.job())
            self.assertFalse(result["valid"])
            self.assertEqual(result["http_status"],401)
            self.assertNotIn("private-secret",str(result))


if __name__=="__main__":unittest.main()
