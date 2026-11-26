"""CLI forwarding only; synthetic results and no upstream requests."""
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch, Mock

from backend import cslive_go_checkout as checkout


class CheckoutInitialRefreshTests(unittest.TestCase):
    def test_default_on_and_explicit_off_keep_credential_ack(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({
            'checkout_url': 'https://pay.example.test/cs_fixture',
            'checkout_id': 'cs_fixture',
        }).encode(), b'')
        for enabled in (None, True, False):
            with self.subTest(enabled=enabled), patch.object(checkout, 'binary_path', return_value=Path(__file__)), \
                 patch('backend.checkout_process.run', return_value=result) as run, \
                 patch.object(checkout.checkout_diagnostics, 'submit'):
                saver = Mock()
                kwargs = {} if enabled is None else {'refresh_before_checkout': enabled}
                self.assertTrue(checkout.create_checkout(session={'access_token':'fixture-at'}, country='PH',
                    plan='plus', proxy_url='http://proxy.example.test:8080', save_credentials=saver, **kwargs)['ok'])
                self.assertEqual('--skip-initial-session-refresh' in run.call_args.args[0], enabled is False)
                self.assertIs(run.call_args.kwargs['save'], saver)
