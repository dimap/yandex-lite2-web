"""Слова активации: определение пакета, статус и прокси к API демона."""
import json
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock, patch

import server


class Resp:
    def __init__(self, raw, ctype="application/json"):
        self.raw, self.headers = raw, {"Content-Type": ctype}

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def panel(platform="plum", shell=None):
    p = server.Panel(Mock())
    p.glagol = SimpleNamespace(platform=platform, host="192.0.2.10")
    p.adb.serial = "192.0.2.10:5555"
    p.adb.shell.return_value = shell or ("0.2.2\n@bin\n@script\n", "", 0)
    return p


class Installed(unittest.TestCase):
    def test_version_and_files(self):
        p = panel()
        info = p._wakeword_installed()
        self.assertTrue(info["installed"])
        self.assertEqual(info["version"], "0.2.2")
        self.assertTrue(info["bin"] and info["script"])

    def test_not_installed(self):
        p = panel(shell=("", "", 0))
        self.assertFalse(p._wakeword_installed()["installed"])

    def test_autostart_marker(self):
        p = panel(shell=("0.2.2\n@bin\n@script\n@auto\n", "", 0))
        self.assertTrue(p._wakeword_installed()["auto"])
        with patch.object(p, "_wakeword_api", return_value=("application/json", {"audio": {"ok": True}})):
            st = p.wakeword_status()
        self.assertTrue(st["autostart"])

    def test_supported_platforms(self):
        self.assertTrue(panel("plum")._wakeword_supported()[0])
        self.assertTrue(panel("yandexmidi")._wakeword_supported()[0])
        self.assertFalse(panel("yandexstation_2")._wakeword_supported()[0])   # Макс
        self.assertEqual(panel("yandexstation_2")._wakeword_supported()[1], "yandexstation_2")


class Api(unittest.TestCase):
    def test_forward_base(self):
        p = panel()
        p.adb.forward.return_value = 4567
        self.assertEqual(p._wakeword_base(), "http://127.0.0.1:4567")
        p.adb.forward.assert_called_once_with(server.WAKE_PORT)

    def test_direct_ip_fallback(self):
        p = panel()
        p.adb.forward.side_effect = server.AdbError("no forward")
        self.assertEqual(p._wakeword_base(), "http://192.0.2.10:8780")

    def test_json_and_wav(self):
        p = panel()
        with patch("server.Panel._wakeword_base", return_value="http://127.0.0.1:1"):
            with patch("urllib.request.urlopen", return_value=Resp(b'{"audio":{"ok":true}}')):
                ctype, data = p._wakeword_api("/api/state")
            self.assertTrue(ctype.startswith("application/json"))
            self.assertTrue(data["audio"]["ok"])
            with patch("urllib.request.urlopen", return_value=Resp(b"RIFF...", "audio/wav")):
                ctype, data = p._wakeword_api("/api/hits/1")
            self.assertEqual(ctype, "audio/wav")
            self.assertEqual(data, b"RIFF...")

    def test_bad_path(self):
        p = panel()
        for path in ("/admin", "/api/../x", ""):
            with self.assertRaises(server.AdbError):
                p._wakeword_api(path)

    def test_api_error(self):
        p = panel()
        err = urllib.error.HTTPError("http://x", 400, "bad", {}, None)
        err.read = lambda: b'{"error":"no audio"}'
        with patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaisesRegex(server.AdbError, "no audio"):
                p._wakeword_api("/api/state")


class Status(unittest.TestCase):
    def test_status_reads_state(self):
        p = panel()
        state = {"audio": {"ok": True, "channel": "vqe_0", "level": 0.2},
                 "words": [{"id": "jarvis", "name": "Джарвис", "enabled": True, "takes": 8}],
                 "hits": [], "enroll": None, "train": {"active": False}, "station": "Лайт 2"}
        with patch.object(p, "_wakeword_api", return_value=("application/json", state)):
            st = p.wakeword_status()
        self.assertTrue(st["installed"] and st["running"] and st["supported"])
        self.assertTrue(st["audio"]["ok"])
        self.assertEqual(st["words"][0]["name"], "Джарвис")
        self.assertEqual(st["version"], "0.2.2")

    def test_status_reports_api_error(self):
        p = panel()
        with patch.object(p, "_wakeword_api", side_effect=server.AdbError("timeout")):
            st = p.wakeword_status()
        self.assertTrue(st["installed"])
        self.assertFalse(st["running"])
        self.assertIn("timeout", st["error"])


class Autostart(unittest.TestCase):
    def test_on_creates_marker_and_hook(self):
        p = panel()
        p._ensure_hook = Mock()
        p.wakeword_status = Mock(return_value={"autostart": True})
        self.assertEqual(p.wakeword_autostart(True), {"autostart": True})
        p.adb.shell.assert_any_call(f"mkdir -p {server.WEB_DIR} && touch {server.WAKE_AUTOSTART}", check=False)
        p._ensure_hook.assert_called_once()

    def test_off_removes_marker_and_drops_hook(self):
        p = panel()
        p._drop_hook_if_unused = Mock()
        p.wakeword_status = Mock(return_value={"autostart": False})
        self.assertEqual(p.wakeword_autostart(False), {"autostart": False})
        p.adb.shell.assert_any_call(f"rm -f {server.WAKE_AUTOSTART}", check=False)
        p._drop_hook_if_unused.assert_called_once()

    def test_requires_package(self):
        p = panel(shell=("", "", 0))
        with self.assertRaisesRegex(server.AdbError, "не установлен"):
            p.wakeword_autostart(True)

    def test_boot_hook_starts_daemon(self):
        self.assertIn(f"sh {server.WAKE_SHARE}/S97wakeword start", server.BOOT_HOOK_SCRIPT)
        self.assertIn(server.WAKE_AUTOSTART, server.BOOT_HOOK_SCRIPT)
        self.assertIn(("слова активации", server.WAKE_AUTOSTART), server.Panel.HOOK_USERS)


class StartStop(unittest.TestCase):
    def test_start_runs_script_and_waits(self):
        p = panel()
        p.wakeword_status = Mock(side_effect=[{"running": False}, {"running": True}])
        with patch("server.time.sleep", return_value=None):
            st = p.wakeword_start()
        self.assertTrue(st["running"])
        call = p.adb.shell.call_args[0][0]
        self.assertIn(f"{server.WAKE_SHARE}/S97wakeword start", call)
        p.adb.shell.assert_any_call(f"sh {server.WAKE_SHARE}/S97wakeword start 2>&1", check=False, timeout=30)

    def test_start_requires_package(self):
        p = panel(shell=("", "", 0))
        with self.assertRaisesRegex(server.AdbError, "не установлен"):
            p.wakeword_start()

    def test_stop_runs_script(self):
        p = panel()
        p.wakeword_status = Mock(return_value={"running": False})
        p.wakeword_stop()
        self.assertIn(f"{server.WAKE_SHARE}/S97wakeword stop", p.adb.shell.call_args[0][0])


class Proxy(unittest.TestCase):
    def test_get_and_method_validation(self):
        p = panel()
        with patch.object(p, "_wakeword_api", return_value=("application/json", {"ok": True})):
            data, ctype = p.wakeword_proxy("get", "/api/state")
        self.assertEqual(data, {"ok": True})
        with self.assertRaises(server.AdbError):
            p.wakeword_proxy("PUT", "/api/state")


if __name__ == "__main__":
    unittest.main()
