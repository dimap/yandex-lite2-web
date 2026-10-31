"""Живой спектр с колонки: состояние, включение/выключение и учёт в «Убрать всё»."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class VizTests(unittest.TestCase):
    def setUp(self):
        self.panel = server.Panel(Mock())

    def test_status_reads_bands_and_running(self):
        state = {"bands": [0.1, 0.2, 0.3, 0.4], "t": 1_700_000_000_000, "rms": 0.25}
        self.panel.adb.shell = lambda *a, **k: (f"@installed\n@running\n@state\n{json.dumps(state)}\n", "", 0)
        st = self.panel.viz_status()
        self.assertTrue(st["installed"] and st["running"])
        self.assertEqual(st["bands"], [0.1, 0.2, 0.3, 0.4])
        self.assertEqual(st["rms"], 0.25)

    def test_error_from_daemon_is_shown(self):
        state = {"error": "arecord завершился"}
        self.panel.adb.shell = lambda *a, **k: (f"@installed\n@running\n@state\n{json.dumps(state)}\n", "", 0)
        st = self.panel.viz_status()
        self.assertIn("arecord", st["error"])
        self.assertEqual(st["bands"], [])

    def test_off_stops_daemon_and_removes_state(self):
        calls = []
        self.panel.adb.shell = lambda cmd, *a, **k: (calls.append(cmd), ("", "", 0))[1]
        st = self.panel.viz_set(False)
        self.assertFalse(st["running"])
        self.assertIn(f"start-stop-daemon -K -p {server.VIZ_SD}", calls[0])
        self.assertIn(server.VIZ_STATUS, calls[0])

    def test_on_starts_installed_program(self):
        self.panel.viz_status = Mock(return_value={"installed": True, "running": False})
        self.panel.viz_start = Mock(return_value={"installed": True, "running": True})
        self.panel.viz_install = Mock()
        st = self.panel.viz_set(True)
        self.assertTrue(st["running"])
        self.panel.viz_start.assert_called_once()
        self.panel.viz_install.assert_not_called()

    def test_on_installs_when_missing(self):
        self.panel.viz_status = Mock(return_value={"installed": False, "running": False})
        self.panel.viz_install = Mock(return_value={"installed": True, "running": True})
        st = self.panel.viz_set(True)
        self.assertTrue(st["running"])
        self.panel.viz_install.assert_called_once()

    def test_footprint_lists_live_spectrum(self):
        values = " ".join(["0"] * 28 + ["1", "2300"])
        self.panel.adb.shell = lambda *a, **k: (values, "", 0)
        item = next(i for i in self.panel.footprint()["items"] if i["key"] == "viz")
        self.assertTrue(item["on"])
        self.assertEqual(item["size_kb"], 2300)

    def test_remove_all_stops_daemon_and_temp_files(self):
        self.panel.adb.only_adb.return_value.__enter__ = Mock()
        self.panel.adb.only_adb.return_value.__exit__ = Mock(return_value=False)
        calls = []
        self.panel.adb.shell = lambda cmd, *a, **k: (calls.append(cmd), ("", "", 0))[1]
        self.panel.ring_reset = lambda: None
        self.panel.footprint = lambda: {"items": []}
        self.panel.footprint_reset()
        joined = " ".join(calls)
        self.assertIn(f"start-stop-daemon -K -p {server.VIZ_SD}", joined)
        self.assertIn("/tmp/webpanel_viz*", joined)


if __name__ == "__main__":
    unittest.main()
