"""Real curl TLS failure against a loopback CONNECT fixture, no upstream traffic."""
import socket
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from curl_cffi import CurlInfo
from manager_oauth import merge_v2
import test_merge_v2
from test_merge_v2 import session


class ExportTLSTests(unittest.TestCase):
    def test_actual_tls_handshake_failure_keeps_member_and_requests_delayed_retry(self):
        listener=socket.socket();listener.bind(("127.0.0.1",0));listener.listen(1);listener.settimeout(5)
        port=listener.getsockname()[1];observed=[];errors=[]
        def server():
            try:
                conn,_=listener.accept()
                with conn:
                    conn.settimeout(5);data=b""
                    while b"\r\n\r\n" not in data:data+=conn.recv(4096)
                    observed.append(data.split(b"\r\n",1)[0])
                    conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    observed.append(conn.recv(16384)[:1])
                    conn.sendall(bytes.fromhex("15030300020228"))
            except Exception as exc:errors.append(type(exc).__name__)
            finally:listener.close()
        thread=threading.Thread(target=server,daemon=True);thread.start()
        flow=test_merge_v2.MergeV2ProtocolTests().flow();self.addCleanup(flow.close)
        flow.web_session.proxies={"http":f"http://127.0.0.1:{port}","https":f"http://127.0.0.1:{port}"}
        original=list(flow.web_session.curl_infos)
        with patch.object(merge_v2.bridge,"call") as rpc:
            result=flow.submit_export(session())
        thread.join(6)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assertEqual(observed,[b"CONNECT chatgpt.com:443 HTTP/1.1",b"\x16"])
        self.assertEqual(result["network_code"],35)
        self.assertEqual(result["error_code"],"export_not_sent")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["evidence"]["transport_state"],"tls_before_request")
        self.assertEqual([c.args[0] for c in rpc.call_args_list],["merge_v2_ready","merge_v2_submitting","merge_v2_rejected"])
        self.assertEqual(flow.web_session.curl_infos,original)

    def test_missing_or_contradictory_metrics_never_authorize_repost(self):
        safe={CurlInfo.REQUEST_SIZE:0,CurlInfo.APPCONNECT_TIME_T:0,CurlInfo.REDIRECT_COUNT:0}
        cases=[({},0,35),(safe,200,35),(safe,0,56),(safe,0,28)]
        for field in safe:
            for value in (1,None,False,"0"):
                cases.append(({**safe,field:value},0,35))
        for infos,status,code in cases:
            exc=SimpleNamespace(code=code,response=SimpleNamespace(status_code=status,infos=infos))
            self.assertFalse(merge_v2.tls_request_not_sent(exc))
        self.assertTrue(merge_v2.tls_request_not_sent(SimpleNamespace(code=35,response=SimpleNamespace(status_code=0,infos=safe))))


if __name__ == "__main__":unittest.main()
