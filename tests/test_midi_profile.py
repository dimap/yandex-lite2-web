"""Midi software coverage. No test in this file uses a physical speaker."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import profiles
from test_profiles import fresh_server


class MidiProfile(unittest.TestCase):
    def setUp(self):
        self.s = fresh_server()
        self.s.Handler.do_GET = Mock()
        self.s.Handler.do_POST = Mock()
        self.get, self.post = self.s.Handler.do_GET, self.s.Handler.do_POST
        profiles.install(self.s, 'midi')
        self.adb = Mock()
        self.p = self.s.Panel(self.adb)
        self.p.glagol = SimpleNamespace(ws=None)

    def test_distinct_identity_and_platform_boundaries(self):
        for identity, expected in [('yandex,cucumber\0amlogic,a5', ('midi','cucumber')),
                                   ('yandex,yandexmidi', ('station2','yandexmidi')),
                                   ('yandex,station-mini2', ('mini2','station-mini2')),
                                   ('yandex,cucumberish', (None,None))]:
            self.adb.shell.side_effect = lambda command, **kwargs: (
                'mini2:white:clock0\n' if command.startswith('ls /sys/class/leds') else identity, '', 0)
            self.assertEqual(self.s.detect_model(self.adb), expected)
        self.adb.shell.side_effect = None
        self.assertEqual(self.s.GLAGOL_PLATFORM, 'cucumber')
        self.assertEqual(self.s.YAM_DIR, fresh_server().YAM_DIR)

    def test_general_trials_are_delegated_instead_of_blanket_read_only_mode(self):
        for path in ('/api/sounds', '/api/music', '/api/files', '/api/assistant', '/api/alarms'):
            request = SimpleNamespace(path=path, _guard=lambda _: False, _send=Mock())
            self.s.Handler.do_GET(request)
            self.get.assert_called_with(request)
            request._send.assert_not_called()
        for path in ('/api/volume', '/api/music/cmd', '/api/terminal', '/api/assistant/install', '/api/alarms'):
            request = SimpleNamespace(path=path, _guard=lambda _: False, _send=Mock())
            self.s.Handler.do_POST(request)
            self.post.assert_called_with(request)
            request._send.assert_not_called()
        profile = profiles.PROFILES['midi']
        self.assertTrue(profile['experimental'])
        self.assertFalse(profile.get('read_only',False))
        self.assertEqual(tuple(profile.get('tested', ())), ('say',))

    def test_hardware_commands_and_clock_upload_refuse_before_adb(self):
        for method,args in [('ring', ({'red':64},)), ('ring_reset', ()), ('clock', ({'text':'1234'},)),
                            ('display_set', ({'mode':'date'},)), ('play_clock', ([],1)),
                            ('clock_frames', ('test.binscled',)), ('display_stop', ()),
                            ('my_anim_save', ('clock','demo'))]:
            with self.subTest(method=method), self.assertRaises(self.s.AdbError):
                getattr(self.p,method)(*args)
        self.adb.shell.assert_not_called()
        self.adb.push.assert_not_called()

    def test_unverified_leds_do_not_read_lite2_or_station2_framebuffer(self):
        self.adb.shell.return_value = ('compatible\tyandex,cucumber amlogic,a5\n', '', 0)
        self.assertEqual(self.p.clock_state(), {'v':None,'ring':[], 'ring_all':[],
                                               'logo':[], 'clock_raw':{}, 'unverified':True})
        command = self.adb.shell.call_args.args[0]
        self.assertIn('/sys/class/leds/cucumber:', command)
        self.assertNotIn('/dev/fb', command)
        self.assertNotIn('plum:', command)
        self.adb.read_bytes.assert_not_called()

    def test_boot_prerequisite_is_checked_without_global_experimental_ban(self):
        self.adb.shell.return_value = ('','',1)
        with self.assertRaisesRegex(self.s.AdbError, 'Автозапуск'):
            self.p._ensure_hook()
        self.adb.push.assert_not_called()
        self.assertIn('echo initd', self.adb.shell.call_args.args[0])
        self.assertNotIn('mktemp', self.adb.shell.call_args.args[0])

    def test_stop_animation_only_uses_panel_pidfiles(self):
        self.p.stop_pattern('ring')
        commands = '\n'.join(c.args[0] for c in self.adb.shell.call_args_list)
        self.assertIn('webpanel_ring.pid',commands)
        self.assertNotIn('plum:',commands)
        self.assertNotIn('/dev/fb0',commands)

    def test_ring_animation_catalog_is_read_from_device_including_own_names(self):
        self.adb.shell.return_value = ('/system/vendor/quasar/ledpatterns/oknix/wait.led\n<<<\n/data/quasar/webpanel/anims/ring/Закат.led\n','',0)
        self.assertEqual(self.p.patterns(), {'ring':['my/Закат.led','wait.led'], 'clock':[]})

    def test_raw_mixer_requires_actual_control_and_respects_limits(self):
        self.adb.shell.return_value = ('type=INTEGER,access=rw---,values=2,min=0,max=170\n','',0)
        self.p.set_volume({'master':220})
        self.assertIn('170,170',self.adb.shell.call_args.args[0])
        self.adb.reset_mock()
        self.adb.shell.return_value = ('','',1)
        with self.assertRaises(self.s.AdbError):
            self.p.set_volume({'master':80})
        self.assertEqual(self.adb.shell.call_count,1)

    def test_mono_mixer_uses_exact_channel_count_and_multiple_controls_preflight_before_write(self):
        self.adb.shell.return_value = ('type=INTEGER,access=rw---,values=1,min=0,max=170\n','',0)
        self.p.set_volume({'master':80})
        self.assertIn(' 80 >/dev/null',self.adb.shell.call_args.args[0])
        self.assertNotIn('80,80',self.adb.shell.call_args.args[0])
        self.adb.reset_mock()
        self.adb.shell.side_effect = [('type=INTEGER,access=rw---,values=2,min=0,max=170\n','',0), ('','',1)]
        with self.assertRaises(self.s.AdbError):
            self.p.set_volume({'master':80,'bt':50})
        self.assertEqual(self.adb.shell.call_count,2)
        self.assertTrue(all('cset' not in c.args[0] for c in self.adb.shell.call_args_list))

    def test_glagol_volume_does_not_probe_or_write_alsa(self):
        self.p.glagol = SimpleNamespace(ws=True,send=Mock())
        self.p.set_volume({'level':25})
        self.p.glagol.send.assert_called_once_with({'command':'setVolume','volume':.25})
        self.adb.shell.assert_not_called()

    def test_midi_alarm_does_not_inherit_lite_leds(self):
        self.p._device_volume_map = lambda: list(range(21))
        script = self.p._alarm_script().decode()
        self.assertNotIn('/sys/class/leds/plum:',script)
        self.assertNotIn('> /dev/fb0',script)
        self.assertIn('ring() { :; }',script)
        # This only validates the absence of unknown LED writes, not Midi alarm support.
