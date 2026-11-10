import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import resource_probe as probe
import refund_runner as runner
from manager_oauth import tls


class ResourceProbeTests(unittest.TestCase):
    def test_seats_respect_held_zero_and_missing(self):
        body={"seat_capacity":[{"type":"default","paid":10,"held":2,"available":5},{"type":"prolite","paid":0,"available":0}],"assigned":{"default":3}}
        self.assertEqual(probe.capacity(body),{"standard":{"total":8,"remaining":5,"used":3,"held":2},"premium":{"total":0,"remaining":0,"used":0,"held":0}})
        self.assertIsNone(probe.capacity({}))
        for value in [True,-1,"10",None]:
            body["seat_capacity"][0]["paid"]=value
            self.assertIsNone(probe.capacity(body))
        self.assertNotIn("premium",probe.capacity({"seat_capacity":[{"type":"default","paid":5,"available":5}]}))

    def test_failed_seats_do_not_invalidate_access(self):
        for status,code in [(401,"unauthorized"),(403,"forbidden"),(429,"rate_limited"),(500,"http")]:
            mother=Mock()
            mother.call.return_value=Mock(status_code=200,json=lambda:{"items":[]})
            mother.session.request.return_value=Mock(status_code=status)
            result=probe.check_mother({"mother":{"team_id":"test-team"}},lambda _:mother,runner.UpstreamFailure)
            self.assertTrue(result["valid"])
            self.assertEqual(result["capacity_code"],code)
            self.assertNotIn("seats",result)
            mother.session.close.assert_called_once()

    def test_network_failure_categories_hide_exception(self):
        for curl,code in [(77,"tls_config"),(60,"tls_verify"),(5,"dns"),(6,"dns"),(28,"timeout"),(7,"connect")]:
            mother=Mock();mother.call.side_effect=runner.UpstreamFailure("mother_network",network_code=curl)
            result=probe.check_mother({},lambda _:mother,runner.UpstreamFailure)
            self.assertFalse(result["valid"]);self.assertEqual(result["code"],code)
            self.assertNotIn("mother_network",json.dumps(result))

    def test_proxy_no_direct_fallback_or_arbitrary_output(self):
        with patch.object(probe.requests,"Session") as factory:
            self.assertFalse(probe.check_proxy({})["valid"]);factory.assert_not_called()
            client=factory.return_value.__enter__.return_value
            client.get.side_effect=[Mock(status_code=500),Mock(status_code=200,json=lambda:{"ip":"203.0.113.7","secret":"private"})]
            result=probe.check_proxy({"proxy":"http://fixture.invalid:8080"})
            self.assertTrue(result["valid"]);self.assertEqual(result["ip_address"],"203.0.113.7")
            self.assertNotIn("private",json.dumps(result))
            self.assertEqual(factory.call_args.kwargs["proxies"],{"http":"http://fixture.invalid:8080","https":"http://fixture.invalid:8080"})
            self.assertTrue(all(c.kwargs["allow_redirects"] is False for c in client.get.call_args_list))
            client.get.side_effect=None;client.get.return_value=Mock(status_code=200,json=lambda:{"ip":"token-private"})
            result=probe.check_proxy({"proxy":"http://fixture.invalid:8080"})
            self.assertFalse(result["valid"]);self.assertNotIn("token-private",json.dumps(result))

    @unittest.skipUnless(os.name=="nt","Windows certificate path regression")
    def test_unicode_ca_copies_trust_bundle_without_disabling_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/"中文证书.pem";source.write_bytes(b"exact-certificate-content")
            with patch.dict(os.environ,{"REQUESTS_CA_BUNDLE":str(source)}),patch.object(tls,"_bundle_dir",None):
                tls.configure_ca_bundle()
                target=Path(os.environ["REQUESTS_CA_BUNDLE"])
                self.assertTrue(str(target).isascii())
                self.assertEqual(target.read_bytes(),source.read_bytes())
                self.assertEqual(os.environ["CURL_CA_BUNDLE"],str(target))
                tls._bundle_dir.cleanup()

if __name__=="__main__":unittest.main()
