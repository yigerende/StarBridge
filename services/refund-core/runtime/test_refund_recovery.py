import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import refund_recovery as recovery
import refund_runner as runner
from manager_oauth import bridge, merge_v2
from test_merge_v2 import session
import test_merge_v2
import test_refund_runner


class RecoveryTests(unittest.TestCase):
    def job(self, step="assigned"):
        job=test_refund_runner.RunnerTests().job(step)
        job["settings"]["recovery"]={"enabled":True}
        return job

    def test_unknown_verification_never_posts_removes_or_accepts(self):
        for state in ["active","missing","deactivated"]:
            job=self.job("submitting");job["recovery_mode"]="verify"
            job["personal_baseline"]={"id":"personal-one","state":"active"}
            flow=Mock();flow.validate_team.return_value=("token","child-user")
            mother=Mock();events=[]
            with patch.object(recovery,"personal_snapshot",return_value={"id":"personal-one" if state!="missing" else "","state":state}):
                result=runner.execute(job,lambda name,*args:events.append(name),lambda _:mother,lambda *_:flow)
            self.assertFalse(result["ok"])
            self.assertEqual(events,["verification_started","verification_result"])
            mother.remove.assert_not_called();mother.invite.assert_not_called()
            flow.submit_export.assert_not_called();flow.close.assert_called_once()
            self.assertNotIn("accepted",str(result))

    def test_baseline_and_changed_identity_never_prove_success(self):
        self.assertEqual(recovery.verification_code(None,{"id":"p","state":"deactivated"}),"verification_no_baseline")
        self.assertEqual(recovery.verification_code({"id":"old","state":"active"},{"id":"new","state":"deactivated"}),"verification_changed")

    def test_verification_checks_child_identity(self):
        job=self.job("submitting");job["recovery_mode"]="verify"
        flow=Mock();flow.validate_team.return_value=("token","wrong-child")
        with patch.object(recovery,"personal_snapshot") as probe:
            result=runner.execute(job,lambda *_:None,lambda _:Mock(),lambda *_:flow)
        self.assertEqual(result["code"],"member_identity");probe.assert_not_called()

    def test_acceptance_cleanup_resume_without_login_or_submission(self):
        mother=Mock();factory=Mock(side_effect=AssertionError("no login"));events=[]
        result=runner.execute(self.job("accepted"),lambda name,*args:events.append(name),lambda _:mother,factory)
        self.assertTrue(result["ok"]);mother.confirm_removed.assert_called_once()
        self.assertEqual(events,["removing","completed"])
        mother.confirm_removed.side_effect=runner.UpstreamFailure("cleanup_pending")
        events.clear()
        result=runner.execute(self.job("accepted"),lambda name,*args:events.append(name),lambda _:mother,factory)
        self.assertFalse(result["ok"]);self.assertNotIn("completed",events)

    def test_failed_cleanup_requires_verified_absence(self):
        job=self.job("login");job["recovery_mode"]="cleanup";job["child_proxy"]=""
        mother=Mock();events=[]
        result=runner.execute(job,lambda name,*args:events.append(name),lambda _:mother,Mock(side_effect=AssertionError("no child request")))
        self.assertTrue(result["ok"]);self.assertEqual(events,["cleanup_started","cleanup_completed"])
        mother.cleanup_unaccepted.side_effect=runner.UpstreamFailure("cleanup_invite_pending")
        events.clear();result=runner.execute(job,lambda name,*args:events.append(name),lambda _:mother)
        self.assertFalse(result["ok"]);self.assertNotIn("cleanup_completed",events)

    def test_retry_is_persisted_not_a_tight_login_loop(self):
        flow=Mock();flow.submit_export.return_value={"success":False,"retryable":True,"error_code":"export_not_sent","network_code":7}
        sleeper=Mock();mother=Mock()
        with patch.object(recovery,"personal_snapshot",return_value={"id":"p","state":"active"}):
            result=runner.execute(self.job(),lambda *_:None,lambda _:mother,lambda *_:flow,sleeper)
        self.assertTrue(result["retryable"]);flow.login.assert_called_once();sleeper.assert_not_called();mother.remove.assert_not_called()

    def test_only_preconnect_errors_clear_submission(self):
        for code in [5,6,7,28,35,52,55,56]:
            flow=test_merge_v2.MergeV2ProtocolTests().flow();self.addCleanup(flow.close)
            exc=RuntimeError("private-secret");exc.code=code
            with patch.object(flow.web_session,"request",side_effect=exc),patch.object(bridge,"call") as rpc:
                result=flow.submit_export(session())
            self.assertEqual(result["retryable"],code in {5,6,7})
            self.assertEqual("merge_v2_rejected" in [c.args[0] for c in rpc.call_args_list],code in {5,6,7})
            self.assertNotIn("private-secret",str(result))

    def test_snapshot_rejects_bad_data_and_keeps_only_identity_state(self):
        flow=Mock();flow.validate_team.return_value=("private-token","child-user")
        flow.web_session.get.return_value.status_code=200
        for data in [{},{"accounts":[]},{"accounts":[{}]},{"accounts":{"a":{"account":{"account_id":"p","structure":"personal","is_deactivated":"false"}}}}]:
            flow.web_session.get.return_value.json.return_value=data
            with self.assertRaises(recovery.VerificationUnavailable):recovery.personal_snapshot(flow,{})
        flow.web_session.get.return_value.json.return_value={"accounts":{"a":{"account":{"account_id":"p","structure":"personal","is_deactivated":False,"email":"private"}}}}
        self.assertEqual(recovery.personal_snapshot(flow,{}),{"id":"p","state":"active"})
        self.assertEqual(flow.web_session.get.call_args.kwargs["timeout"],45)

if __name__=="__main__":unittest.main()
