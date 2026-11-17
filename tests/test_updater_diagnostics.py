"""Private diagnostic provisioning must work below the root-owned update mount."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from scripts import starbridge_updater as updater


class DiagnosticDirectoryTests(unittest.TestCase):
    def test_install_and_restart_preserve_logs_and_set_app_ownership(self):
        with tempfile.TemporaryDirectory() as root, patch.object(updater,'PUBLIC',Path(root)), \
             patch.object(updater.os,'chown',create=True) as chown:
            updater.prepare_diagnostics_directory()
            target=Path(root)/'diagnostics'
            existing=target/'checkout.jsonl';existing.write_text('existing')
            updater.prepare_diagnostics_directory()
            chown.assert_called_with(target,1000,1000,follow_symlinks=False)
            self.assertEqual(existing.read_text(),'existing')
            if os.name!='nt':self.assertEqual(target.stat().st_mode&0o777,0o700)

    @unittest.skipIf(os.name=='nt','Linux updater symlink boundary')
    def test_symlink_diagnostic_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside, \
             patch.object(updater,'PUBLIC',Path(root)),patch.object(updater.os,'chown') as chown:
            (Path(root)/'diagnostics').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(updater.UpdateError):updater.prepare_diagnostics_directory()
            chown.assert_not_called()


if __name__=='__main__':unittest.main()
