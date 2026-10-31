"""Regression tests use local fake sysfs/ADB; they never contact a speaker."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import profiles
from test_profiles import fresh_server


class RingRollback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'before'
        self.values = {}
        for i in range(1, 7):
            for c in ('red', 'green', 'blue'):
                n = f'mini2:{c}:ring{i}'
                d = self.root / n
                d.mkdir()
                self.values[n] = i * 9
                (d / 'brightness').write_text(str(i * 9) + '\n')
                (d / 'trigger').write_text('[none] timer\n')

    def run_ring(self, rgb=None):
        return subprocess.run(['sh', '-c', profiles.mini2_ring_script(rgb, str(self.root), str(self.state))],
                              capture_output=True, timeout=5)

    def read(self):
        return {n: int((self.root / n / 'brightness').read_text()) for n in self.values}

    def test_multiple_colours_restore_first_snapshot_and_reset_without_snapshot_is_noop(self):
        original = self.read()
        for color in ((40, 0, 0), (0, 40, 0), (0, 0, 40)):
            self.assertEqual(self.run_ring(color).returncode, 0)
            for n, value in self.read().items():
                self.assertEqual(value, color[('red', 'green', 'blue').index(n.split(':')[1])])
        self.assertEqual(self.run_ring().returncode, 0)
        self.assertEqual(self.read(), original)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.run_ring().returncode, 0)
        self.assertEqual(self.read(), original)

    def test_missing_led_does_not_change_any_other_channel_or_leave_snapshot(self):
        (self.root / 'mini2:blue:ring6' / 'brightness').unlink()
        before = {p: p.read_bytes() for p in self.root.glob('*/brightness')}
        self.assertNotEqual(self.run_ring((1, 2, 3)).returncode, 0)
        self.assertFalse(self.state.exists())
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_corrupt_snapshot_does_not_partially_restore(self):
        self.state.write_text('mini2:red:ring1 1\n')
        self.assertNotEqual(self.run_ring().returncode, 0)
        self.assertEqual(self.read(), self.values)
        self.assertTrue(self.state.exists())


class AudioAdapter(unittest.TestCase):
    def setUp(self):
        self.s = fresh_server()
        self.s.Panel.status = lambda p: {'vol_master': '102', 'vol_level': 30}
        profiles.install(self.s, 'mini2')
        self.adb = Mock()
        self.p = self.s.Panel(self.adb)
        self.p.glagol = SimpleNamespace(ws=None, info=lambda: {'connected': False, 'state': None})
        self.table = [round(i * 255 / 20) for i in range(21)]
        self.adb.shell.return_value = (json.dumps({'volume_manager_settings': {'platformToAlsaMap': self.table}}), '', 0)

    def test_glagol_state_is_authoritative_instead_of_lite2_mapping(self):
        self.p.glagol.info = lambda: {'connected': True, 'state': {'volume': .4}}
        self.assertEqual(self.p.status()['vol_level'], 40)
        self.assertEqual(self.p.status()['volume_source'], 'glagol')
        self.adb.shell.assert_not_called()

    def test_device_mapping_is_used_for_status_and_alsa_fallback(self):
        self.assertEqual(self.p.status()['vol_level'], 40)
        self.p.set_volume({'level': 35})
        self.assertIn('89,89', self.adb.shell.call_args.args[0])

    def test_missing_map_is_not_replaced_with_lite2_and_never_writes(self):
        self.adb.shell.return_value = ('{}', '', 0)
        self.assertIsNone(self.p.status()['vol_level'])
        self.adb.reset_mock()
        with self.assertRaises(self.s.AdbError):
            self.p.set_volume({'level': 35})
        self.assertEqual(self.adb.shell.call_count, 1)
        self.assertTrue(self.adb.shell.call_args.args[0].startswith('cat '))

    def test_rejects_malformed_tables(self):
        for table in ([], [0] * 21, list(range(20)), [0] + [True] * 20,
                      list(range(20)) + [256], list(reversed(range(21)))):
            with self.subTest(table=table), self.assertRaises(ValueError):
                profiles.volume_map({'volume_manager_settings': {'platformToAlsaMap': table}})

    def test_alarm_script_uses_device_volume_and_mini2_ring(self):
        script = self.p._alarm_script().decode()
        self.assertNotIn('/sys/class/leds/plum:', script)
        self.assertNotIn('49 + (p - 1)', script)
        self.assertIn('mini2:red:ring6', script)
        self.assertIn('/tmp/webpanel_alarm_ring.before', script)
        self.assertEqual(subprocess.run(['sh', '-n'], input=script.encode(), capture_output=True).returncode, 0)


class BootPreflight(unittest.TestCase):
    def test_no_boot_reference_rejects_before_any_device_mutation(self):
        s = fresh_server()
        profiles.install(s, 'mini2')
        adb = Mock()
        adb.shell.return_value = ('', '', 1)
        p = s.Panel(adb)
        for method, args in (('alarms_save', ({'alarms': []},)), ('scenes_save', ({'scenes': []},)),
                             ('alarm_sound_upload', ('test.wav', b'')), ('_ensure_hook', ()),
                             ('sound_replace', ('test.wav', b'')), ('anim_install', ('ring', 'test.led')),
                             ('privacy_set', (True,)), ('boot_always', (True,))):
            with self.subTest(method=method), self.assertRaisesRegex(s.AdbError, 'Автозапуск'):
                getattr(p, method)(*args)
        adb.push.assert_not_called()
        self.assertEqual(adb.shell.call_count, 8)
        self.assertTrue(all('echo initd' in c.args[0] for c in adb.shell.call_args_list))
        self.assertTrue(all('mktemp' not in c.args[0] for c in adb.shell.call_args_list))

    def test_timeout_does_not_write(self):
        s = fresh_server()
        profiles.install(s, 'mini2')
        adb = Mock()
        adb.shell.side_effect = s.AdbError('timeout')
        with self.assertRaises(s.AdbError):
            s.Panel(adb).alarms_save({'alarms': []})
        adb.push.assert_not_called()

    def test_prerequisite_passes_to_original_and_does_not_gate_rollback(self):
        s = fresh_server()
        with patch.object(s.Panel, 'boot_always', return_value={'ok': True}) as original:
            profiles.install(s, 'mini2')
            p = s.Panel(Mock())
            p.adb.shell.return_value = ('rootkeeper\n', '', 0)
            p.boot_always(True)
            p.adb.shell.assert_called_once()
            p.adb.reset_mock()
            p.boot_always(False)
            p.adb.shell.assert_not_called()
            self.assertEqual(original.call_count, 2)
