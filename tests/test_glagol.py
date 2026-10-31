import json
import ssl
import subprocess
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock, patch

import glagol
import server


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
        for platform in ("plum", "pickle", "yandexmidi", "bergamot", "yandexmini_2", "cucumber"):
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


class GlagolTokenTests(unittest.TestCase):
    def test_certificate_error_is_explained(self):
        g = glagol.Glagol.__new__(glagol.Glagol)
        g.device_id, g.platform = "id", "plum"
        g.auth = SimpleNamespace(music_token="token")
        err = urllib.error.URLError(ssl.SSLCertVerificationError("bad cert"))
        with patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(glagol.AuthError) as cm:
                g._conversation_token()
        self.assertIn("сертификат", str(cm.exception))


class AliceCommandLimitTests(unittest.TestCase):
    def test_limits_checked_before_any_glagol_send(self):
        panel = server.Panel(Mock())
        panel.glagol = Mock()
        for text in [None, [], 7, '', '   ', 'я' * 1025, '😀' * 1025]:
            with self.subTest(text_type=type(text).__name__), self.assertRaises(server.AdbError):
                panel.music_cmd({'command': 'sendText', 'text': text})
        panel.glagol.send.assert_not_called()
        for text in ['я' * 1024, '😀' * 1024]:
            panel.music_cmd({'command': 'sendText', 'text': '  ' + text + '  '})
            panel.glagol.send.assert_called_with({'command': 'sendText', 'text': text})
