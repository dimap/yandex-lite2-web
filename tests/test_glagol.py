import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import server
import glagol


class MusicTrackTests(unittest.TestCase):
    def test_short_track_accepts_missing_title_and_preserves_existing_metadata(self):
        music = object.__new__(glagol.YandexMusic)
        cases = [({}, ''), ({'title': None}, ''), ({'title': ''}, ''),
                 ({'title': 'Example'}, 'Example'),
                 ({'title': 'Example', 'version': 'Live'}, 'Example (Live)'),
                 ({'title': None, 'version': 'Live'}, ' (Live)')]
        for fields, title in cases:
            with self.subTest(fields=fields):
                track = dict(id=123, artists=[{'name': 'Artist'}], durationMs=120000,
                             coverUri='img.example.invalid/%%', available=False, **fields)
                self.assertEqual(music._short(track),
                                 dict(id='123', title=title, artists='Artist', durationMs=120000,
                                      cover='https://img.example.invalid/100x100', available=False))

    def test_radio_queue_survives_tracks_without_a_title(self):
        music = object.__new__(glagol.YandexMusic)
        tracks = {'1': {'id': 1, 'title': None},
                  '2': {'id': 2, 'title': 'Current', 'version': 'Live'},
                  '3': {'id': 3}}
        music._raw = Mock(side_effect=lambda tid: tracks[str(tid)])
        queue = music.queue({'id': 2, 'playlistType': 'Radio',
                             'entityInfo': {'prev': {'id': 1}, 'next': {'id': 3}}})
        self.assertEqual([t['id'] for t in queue['tracks']], ['1', '2', '3'])
        self.assertEqual([t['title'] for t in queue['tracks']], ['', 'Current (Live)', ''])
        self.assertEqual(queue['current'], 1)
        self.assertEqual(queue['type'], 'Radio')


class GlagolIdentityTests(unittest.TestCase):
    def panel(self, config, host="192.0.2.10", platform="plum"):
        adb = Mock()
        adb.shell.return_value = (config, "", 0)
        panel = server.Panel(adb)
        panel.glagol = SimpleNamespace(platform=platform, device_id=None, host=host, wake=Mock())
        return panel

    def test_lite_uses_firmware_platform_before_connecting(self):
        panel = self.panel(json.dumps({"common": {"deviceType": "yandexmicro"}}))
        observed = []
        panel.glagol.wake.set.side_effect = lambda: observed.append(
            (panel.glagol.device_id, panel.glagol.platform))
        panel._configure_glagol("test-device")
        self.assertEqual(observed, [("test-device", "yandexmicro")])
        self.assertEqual(panel.glagol.host, "192.0.2.10")
        panel.adb.shell.assert_called_once_with(
            "cat /system/vendor/quasar/quasar.cfg 2>/dev/null", check=False)

    def test_other_firmware_platforms_are_not_replaced_with_lite(self):
        for platform in ("plum", "pickle", "yandexmidi", "bergamot", "yandexmini_2"):
            with self.subTest(platform=platform):
                panel = self.panel(json.dumps({"common": {"deviceType": platform}}))
                panel._configure_glagol("test-device")
                self.assertEqual(panel.glagol.platform, platform)

    def test_unavailable_or_invalid_config_keeps_profile_platform(self):
        for config in ("", "not json", "[]", "{}", '{"common": []}',
                       '{"common": {"deviceType": null}}',
                       '{"common": {"deviceType": "bad platform"}}'):
            with self.subTest(config=config):
                panel = self.panel(config, platform="pickle")
                panel._configure_glagol("test-device")
                self.assertEqual(panel.glagol.platform, "pickle")
                self.assertEqual(panel.glagol.device_id, "test-device")
                panel.glagol.wake.set.assert_called_once()

    def test_config_read_failure_does_not_break_existing_connection_setup(self):
        for error in (server.AdbError("offline"), OSError("read failed"),
                      subprocess.TimeoutExpired("adb", 15)):
            with self.subTest(error=error):
                panel = self.panel("")
                panel.adb.shell.side_effect = error
                panel._configure_glagol("test-device")
                self.assertEqual(panel.glagol.platform, "plum")
                panel.glagol.wake.set.assert_called_once()

    def test_usb_connection_still_discovers_wifi_address(self):
        panel = self.panel("", host=None)
        panel.adb.shell.side_effect = [
            ('{"common": {"deviceType": "yandexmicro"}}', "", 0),
            ("192.0.2.20\n", "", 0),
        ]
        panel._configure_glagol("usb-device")
        self.assertEqual(panel.glagol.host, "192.0.2.20")
        self.assertEqual(panel.glagol.platform, "yandexmicro")

    def test_missing_device_id_does_not_start_a_connection(self):
        panel = self.panel('{"common": {"deviceType": "yandexmicro"}}')
        panel._configure_glagol("")
        self.assertIsNone(panel.glagol.device_id)
