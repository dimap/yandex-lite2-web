import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class Restart(unittest.TestCase):
    def test_hook_instead_of_exec(self):
        got = []
        argv = ["server.py", "--port", "8765", "--device", "10.0.0.1:5555", "--open"]
        with patch.object(sys, "argv", argv), patch.object(server, "RESTART_HOOK", got.append), \
                patch.object(server.os, "execv", side_effect=AssertionError("exec на Android")):
            server.restart_self(device="10.0.0.2:5555")
        self.assertEqual(got, [["--port", "8765", "--device", "10.0.0.2:5555"]])

    def test_android_install_never_self_updates(self):
        with patch.dict(os.environ, {"PANEL_INSTALL": "android"}):
            self.assertEqual(server.PanelUpdater.kind(), "android")
            u = server.PanelUpdater(restart=lambda: None)
            u.check = lambda refresh=False: {"install": "android", "update": True, "latest": "9.9.9"}
            with self.assertRaisesRegex(server.AdbError, "APK"):
                u.update()


if __name__ == "__main__":
    unittest.main()
