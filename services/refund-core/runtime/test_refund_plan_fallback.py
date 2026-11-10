"""Offline recovery contracts; never changes a real account."""
import copy
import base64
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import refund_plan_fallback as fallback
import refund_runner as runner
from test_merge_v2 import session
import test_refund_runner
from test_refund_transport import ProxyFlow


class PersonalProxyFlow(ProxyFlow):
    def __init__(self, job, plan):
        super().__init__(job)
        self.plan = plan
        self.members = True
        claims = {"email": job["account"]["email"], "https://api.openai.com/auth": {
            "chatgpt_account_id": "personal", "chatgpt_user_id": "child-user"}}
        self.token = "x." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".x"

    def reply(self, role, method, url, headers, raw):
        email = self.job["account"]["email"]
        if url.path == "/workspace":
            cookie = base64.urlsafe_b64encode(json.dumps({"workspaces":[{"id":"personal","type":"personal"}]}).encode()).decode().rstrip("=")
            return 200, {}, {"Set-Cookie":"oai-client-auth-session="+cookie+"; Path=/"}
        if url.path == "/api/accounts/workspace/select":
            assert role == "child" and not self.members
            assert json.loads(raw) == {"workspace_id":"personal"}
            return 200, {"continue_url":"https://chatgpt.com/api/auth/callback/openai?code=fixture-code"}, {}
        if url.path == "/api/auth/session":
            assert role == "child" and not self.members and "fixture_team=selected" in headers.get("Cookie", "")
            return 200, {"accessToken":self.token,"account":{"id":"personal"},"user":{"email":email}}, {}
        if url.path in {"/backend-api/me", "/backend-api/accounts/check/v4-2023-04-27"}:
            assert role == "child" and method == "GET" and headers["Authorization"] == "Bearer "+self.token
            if url.path.endswith("/me"): return 200, {"email":email}, {}
            account = {"account_id":"personal","structure":"personal"}
            entitlement = {"subscription_plan":self.plan}
            if isinstance(self.plan, dict):
                account.update(self.plan["account"])
                entitlement = self.plan["entitlement"]
            return 200, {"accounts":{"personal":{"account":account,"entitlement":entitlement}}}, {}
        return super().reply(role, method, url, headers, raw)


class FallbackTests(unittest.TestCase):
    def job(self):
        job = test_refund_runner.RunnerTests().job("submitting")
        job.update(recovery_mode="fallback", fallback_phase="", child_user_id="child-user")
        job["settings"]["recovery"] = {"enabled": True}
        return job

    def test_plan_requires_exact_live_personal_identity_and_explicit_value(self):
        def payload(value):
            return {"accounts": {"personal": {"account": {"account_id": "personal", "structure": "personal"}, "entitlement": {"subscription_plan": value}}}}
        self.assertEqual(fallback.parse_plan(payload("chatgptfreeplan"), "personal"), "free")
        self.assertEqual(fallback.parse_plan(payload("chatgptproplan"), "personal"), "paid")
        for data in [{}, {"accounts": {}}, payload("future-plan"), payload("chatgptteamplan")]:
            with self.assertRaises(ValueError): fallback.parse_plan(data, "personal")
        data = payload("chatgptfreeplan")
        data["accounts"]["personal"]["account"]["structure"] = "workspace"
        with self.assertRaises(ValueError): fallback.parse_plan(data, "personal")
        with self.assertRaises(ValueError): fallback.parse_plan(payload("chatgptfreeplan"), "different")

    def test_current_free_without_subscription_overrides_retained_plan(self):
        for retained in ("chatgptpro", "chatgptproplan", "chatgptplus", "future-plan", None):
            data = {"accounts":{"personal":{
                "account":{"account_id":"personal","structure":"personal","plan_type":"free","plan_display_name":"Free"},
                "entitlement":{"has_active_subscription":False,"subscription_plan":retained}}}}
            self.assertEqual(fallback.parse_plan(data,"personal"),"free")

    def test_active_paid_aliases_and_ambiguous_plan_signals(self):
        def payload(current, retained, active):
            return {"accounts":{"personal":{
                "account":{"account_id":"personal","structure":"personal","plan_type":current},
                "entitlement":{"has_active_subscription":active,"subscription_plan":retained}}}}
        for current, retained in (("pro","chatgptpro"),("plus","chatgptplus"),("prolite","chatgptprolite"),("go","chatgptgoplan")):
            self.assertEqual(fallback.parse_plan(payload(current,retained,True),"personal"),"paid")
        for current, retained, active in (("free","chatgptpro",True),("pro","chatgptpro",False),
                ("free","chatgptpro",None),("pro","chatgptfreeplan",None),
                (None,"chatgptpro",False),(None,None,False),("future-plan",None,False),
                ("free","chatgptpro","false"),("free","chatgptpro",0)):
            with self.subTest(current=current,retained=retained,active=active),self.assertRaises(ValueError):
                fallback.parse_plan(payload(current,retained,active),"personal")

    def test_retained_paid_label_completes_real_proxy_fallback_without_resubmit(self):
        job=self.job();events=[]
        plan={"account":{"plan_type":"free","plan_display_name":"Free"},
              "entitlement":{"has_active_subscription":False,"subscription_plan":"chatgptpro"}}
        with PersonalProxyFlow(job,plan) as fixture:
            result=runner.execute(job,lambda name,*_:events.append(name))
        self.assertEqual(fixture.errors,[])
        self.assertTrue(result["ok"])
        self.assertEqual(events.count("fallback_free"),1)
        self.assertNotIn("fallback_paid",events)
        self.assertTrue(any(r[2]=="/backend-api/me" for r in fixture.records))
        self.assertFalse(any(r[1]=="POST" and (r[2].endswith("/invites") or r[2].endswith("export_and_deactivate")) for r in fixture.records))

    def test_free_completes_after_removal_and_fresh_login_paid_only_schedules(self):
        for plan in ("free", "paid"):
            job = self.job(); events = []; mother = Mock(); flow = Mock()
            flow.login.return_value = session("personal", job["account"]["email"])
            def checkpoint(name, *args): events.append(name)
            with patch.object(fallback, "check_plan", return_value=plan):
                result = fallback.execute(job, checkpoint, mother, lambda *_: flow)
            self.assertEqual(result["ok"], plan == "free")
            self.assertEqual(events, ["fallback_removing", "fallback_removed", "fallback_login", "fallback_plan_started", "fallback_"+plan])
            mother.remove.assert_called_once_with("child-user")
            mother.confirm_removed.assert_called_once(); mother.invite.assert_not_called()
            flow.login.assert_called_once(); flow.submit_export.assert_not_called(); flow.close.assert_called_once()

    def test_unknown_plan_login_or_removal_never_accepts_or_reinvites(self):
        for point in ("remove", "confirm_removed", "login", "plan"):
            job = self.job(); events = []; mother = Mock(); flow = Mock()
            flow.login.return_value = session("personal", job["account"]["email"])
            if point in ("remove", "confirm_removed"): getattr(mother, point).side_effect=RuntimeError("private error")
            if point == "login": flow.login.side_effect=RuntimeError("private error")
            with patch.object(fallback, "check_plan", side_effect=ValueError("private error")):
                result = fallback.execute(job, lambda n,*_:events.append(n), mother, lambda *_:flow)
            self.assertFalse(result["ok"])
            self.assertNotIn("fallback_free",events);self.assertNotIn("fallback_paid",events)
            self.assertNotIn("private error",str(result));mother.invite.assert_not_called()

    def test_resume_after_removal_does_not_remove_again(self):
        job=self.job();job["fallback_phase"]="checking"
        mother=Mock();flow=Mock();flow.login.return_value=session("personal",job["account"]["email"])
        with patch.object(fallback,"check_plan",return_value="free"):
            result=fallback.execute(job,lambda *_:None,mother,lambda *_:flow)
        self.assertTrue(result["ok"]);mother.remove.assert_not_called();mother.confirm_removed.assert_called_once()

    def test_configured_status_paid_result_cannot_schedule_reinvite(self):
        job=self.job();job['verification_only']=True
        events=[];mother=Mock();flow=Mock();flow.login.return_value=session('personal',job['account']['email'])
        with patch.object(fallback,'check_plan',return_value='paid'):
            result=fallback.execute(job,lambda name,*_:events.append(name),mother,lambda *_:flow)
        self.assertFalse(result['ok']);self.assertFalse(result['retryable'])
        self.assertEqual(result['code'],'fallback_paid_unconfirmed')
        self.assertNotIn('fallback_paid',events)
        flow.submit_export.assert_not_called();mother.invite.assert_not_called()

    def test_wrong_user_email_or_team_cannot_query_or_finish(self):
        job=self.job()
        for value in (session(job["mother"]["team_id"],job["account"]["email"]),session("personal","wrong@example.test")):
            flow=Mock();flow.login.return_value=value
            with patch.object(fallback,"check_plan") as check:
                result=fallback.execute(job,lambda *_:None,Mock(),lambda *_:flow)
            self.assertFalse(result["ok"]);check.assert_not_called()

    def test_live_queries_use_login_session_no_jwt_plan_shortcut(self):
        job=self.job();flow=Mock();flow.device_id="fixture"
        sess=session("personal",job["account"]["email"])
        responses=[Mock(status_code=200),Mock(status_code=200)]
        responses[0].json.return_value={"email":job["account"]["email"]}
        responses[1].json.return_value={"accounts":{"personal":{"account":{"account_id":"personal","structure":"personal"},"entitlement":{"subscription_plan":"chatgptfreeplan"}}}}
        flow.web_session.get.side_effect=responses
        self.assertEqual(fallback.check_plan(flow,sess,job),"free")
        self.assertEqual(flow.web_session.get.call_count,2)
        for call in flow.web_session.get.call_args_list:
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertEqual(call.kwargs["headers"]["Authorization"],"Bearer "+sess["accessToken"])
        responses[1].status_code=401;flow.web_session.get.side_effect=responses
        with self.assertRaises(RuntimeError):fallback.check_plan(flow,sess,job)

    def test_runner_dispatch_never_enters_team_login_in_fallback(self):
        job=self.job();mother=Mock();factory=Mock(side_effect=AssertionError("team login forbidden"))
        with patch.object(fallback,"execute",return_value={"ok":True}) as execute:
            result=runner.execute(job,lambda *_:None,lambda _:mother,factory)
        self.assertTrue(result["ok"]);factory.assert_not_called();execute.assert_called_once();mother.session.close.assert_called_once()

    def test_actual_transport_removal_password_totp_personal_login_and_plan(self):
        for plan in ("chatgptfreeplan", "chatgptproplan", "unknown"):
            job=self.job(); events=[]
            with PersonalProxyFlow(job, plan) as fixture:
                result=runner.execute(job,lambda name,*_:events.append(name))
            self.assertEqual(fixture.errors, [])
            self.assertEqual(result["ok"], plan=="chatgptfreeplan")
            self.assertFalse(fixture.members)
            self.assertIn("fallback_removed",events)
            self.assertIn("password_verify",fixture.sent_tokens)
            self.assertTrue(any(r[2]=="/backend-api/me" for r in fixture.records))
            self.assertFalse(any(r[1]=="POST" and (r[2].endswith("/invites") or r[2].endswith("export_and_deactivate")) for r in fixture.records))


if __name__ == "__main__": unittest.main()
