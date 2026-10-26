import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import server


class AdbTransferTests(unittest.TestCase):
    def test_monitor_does_not_interrupt_sync_transfer(self):
        adb = server.Adb('adb', '192.0.2.1:5555')
        conn = server.Connection(adb)
        for fail in (False, True):
            def run(args, **kwargs):
                if 'push' in args:
                    self.assertTrue(adb.sync_busy.is_set())
                    self.assertTrue(conn._alive())  # Must not launch a second shell probe.
                    return SimpleNamespace(returncode=int(fail), stdout=b'', stderr=b'upload failed')
                return SimpleNamespace(returncode=0, stdout=b'ok\n', stderr=b'')
            with patch.object(server.subprocess, 'run', side_effect=run) as process:
                if fail:
                    with self.assertRaises(server.AdbError):
                        adb.push('local', '/tmp/remote')
                else:
                    adb.push('local', '/tmp/remote')
                self.assertFalse(adb.sync_busy.is_set())
                self.assertEqual(process.call_count, 1)
                self.assertTrue(conn._alive())
                self.assertEqual(process.call_count, 2)


class AdbDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="adb discovery ")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        for context in (
            patch.dict(os.environ, {"ADB": "", "ANDROID_HOME": "", "ANDROID_SDK_ROOT": ""}),
            patch.object(server, "ROOT", self.home / "project"),
            patch.object(server.shutil, "which", return_value=None),
            patch.object(Path, "home", return_value=self.home),
        ):
            context.start()
            self.addCleanup(context.stop)

    def executable(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return str(path)

    def test_android_studio_on_macos_without_path_entry(self):
        adb = self.executable(self.home / "Library/Android/sdk/platform-tools/adb")
        with patch.object(server.sys, "platform", "darwin"):
            self.assertEqual(server.find_adb(), adb)

    def test_custom_sdk_directory_with_spaces(self):
        executable = "adb.exe" if os.name == "nt" else "adb"
        sdk = self.home / "custom Android SDK"
        adb = self.executable(sdk / "platform-tools" / executable)
        for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: str(sdk)}):
                self.assertEqual(server.find_adb(), adb)

    def test_explicit_adb_takes_precedence_over_macos_sdk(self):
        self.executable(self.home / "Library/Android/sdk/platform-tools/adb")
        explicit = self.executable(self.home / "chosen tools/adb")
        with patch.object(server.sys, "platform", "darwin"), patch.dict(os.environ, {"ADB": explicit}):
            self.assertEqual(server.find_adb(), explicit)
