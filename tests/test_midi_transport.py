import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import profiles
from test_profiles import fresh_server


class MidiTransportTests(unittest.TestCase):
    def setUp(self):
        self.s = fresh_server()
        profiles.install(self.s, 'midi')
        self.adb = self.s.Adb('adb', 'explicit-midi-target')

    def remote(self, args, **kwargs):
        command = args[-1]
        marker = re.search(r'__PANEL_[a-f0-9]+__', command)[0]
        code = 23 if getattr(self, 'remote_failure', False) and 'rm -f' not in command else 0
        return SimpleNamespace(stdout=f'{marker}\nresult\n{marker}RC={code}\n'.encode(),
                               stderr=b'', returncode=0)

    def test_long_command_uses_private_sync_file_and_removes_it(self):
        source = 'echo ' + 'x' * 5000
        files = []
        def push(local, remote, **kwargs):
            local = Path(local)
            files.append((local.read_text(), remote, local.stat().st_mode & 0o777))
        self.adb.push = Mock(side_effect=push)
        with patch.object(self.s.subprocess, 'run', side_effect=self.remote) as run:
            self.assertEqual(self.adb.shell(source)[2], 0)
        self.assertEqual(files[0][0], source)
        self.assertEqual(files[0][2], 0o600)
        self.assertRegex(files[0][1], r'^/tmp/webpanel_cmd_[a-f0-9]{24}\.sh$')
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(source not in c.args[0][-1] for c in run.call_args_list))
        self.assertIn('rm -f', run.call_args_list[-1].args[0][-1])

    def test_remote_failure_is_not_reported_as_success_and_still_cleans_up(self):
        self.remote_failure = True
        self.adb.push = Mock()
        with patch.object(self.s.subprocess, 'run', side_effect=self.remote) as run:
            with self.assertRaises(self.s.AdbError):
                self.adb.shell('false\n' * 1000)
        self.assertEqual(run.call_count, 2)
        self.assertIn('rm -f', run.call_args_list[-1].args[0][-1])

    def test_short_command_does_not_transfer_files(self):
        self.adb.push = Mock()
        with patch.object(self.s.subprocess, 'run', side_effect=self.remote):
            self.adb.shell('echo ok')
        self.adb.push.assert_not_called()

    def test_device_id_ignores_trailing_daemon_logs(self):
        adb = Mock()
        adb.shell.side_effect = [('factory-9', '', 0),
                                 ('Device ID: EXAMPLE_DEVICE\n[warning] metricad unavailable\n', '', 0)]
        panel = self.s.Panel(adb)
        panel._configure_glagol = Mock()
        self.assertEqual(panel.version()['device_id'], 'EXAMPLE_DEVICE')
        panel._configure_glagol.assert_called_once_with('EXAMPLE_DEVICE')
        self.assertNotIn('tail -1', adb.shell.call_args.args[0])

    def test_missing_device_id_is_unknown_instead_of_log_line(self):
        adb = Mock()
        adb.shell.side_effect = [('factory-9', '', 0), ('[warning] metricad unavailable', '', 0)]
        panel = self.s.Panel(adb)
        panel._configure_glagol = Mock()
        self.assertEqual(panel.version()['device_id'], '')
        panel._configure_glagol.assert_called_once_with('')


if __name__ == '__main__':
    unittest.main()
