"""Эквалайзер Bluetooth-выхода: пресеты, АЧХ и запись коэффициентов на колонку."""
import math
import unittest
from unittest.mock import Mock, patch

import server


class Eq(unittest.TestCase):
    def test_presets_are_valid(self):
        for name in server.EQ_PRESETS:
            for band in server.EQ_PRESETS[name]:
                for v in server.eq_coeffs(band):
                    self.assertTrue(math.isfinite(v))
            freqs, db = server.eq_curve(name)
            self.assertEqual(len(freqs), len(db))
            self.assertEqual(len(freqs), 96)

    def test_flat_curve_is_zero(self):
        _, db = server.eq_curve("flat")
        self.assertTrue(all(abs(x) < 1e-9 for x in db))

    def test_rock_boosts_bass_and_treble(self):
        freqs, db = server.eq_curve("rock")
        at = lambda hz: db[min(range(len(freqs)), key=lambda i: abs(freqs[i] - hz))]
        self.assertGreater(at(60), 1.5)
        self.assertLess(at(800), -0.5)
        self.assertGreater(at(12000), 0.5)

    def test_custom_bands(self):
        p = server.Panel(Mock())
        p.adb.push = lambda *a, **kw: None
        p.adb.shell = lambda *a, **kw: ("", "", 0)
        saved = {}
        bands = [{"f": 80, "g": 6}, {"f": 250, "g": -3}, {"f": 1000, "g": 0},
                 {"f": 4000, "g": 3}, {"f": 12000, "g": 2}]
        with patch("server.load_settings", return_value={}), \
             patch("server.save_settings", side_effect=lambda d: saved.update(d)):
            r = p.btout_eq("custom", bands)
        self.assertEqual(r["preset"], "custom")
        self.assertEqual(saved["eq_custom"][0], {"f": 80.0, "g": 6.0})
        self.assertEqual(len(r["custom"]), 5)
        self.assertTrue(any(abs(x) > 1 for x in r["db"]))

    def test_custom_rejects_bad_values(self):
        p = server.Panel(Mock())
        with self.assertRaises(server.AdbError):
            p.btout_eq("custom", [{"f": 5, "g": 0}])
        with self.assertRaises(server.AdbError):
            p.btout_eq("custom", [{"f": 100, "g": 40}])

    def test_btout_eq_writes_filters_and_saves(self):
        p = server.Panel(Mock())
        pushed, cmds = [], []
        p.adb.push = lambda local, remote, **kw: pushed.append((local, remote))
        p.adb.shell = lambda cmd, *a, **kw: (cmds.append(cmd), ("", "", 0))[1]
        saved = {}
        with patch("server.load_settings", return_value={}), \
             patch("server.save_settings", side_effect=lambda d: saved.update(d)):
            r = p.btout_eq("rock")
        self.assertEqual(r["preset"], "rock")
        self.assertEqual(saved.get("eq_preset"), "rock")
        self.assertTrue(any(rem.endswith("eq.json.new") for _, rem in pushed))
        self.assertEqual(len(r["freqs"]), len(r["db"]))

    def test_unknown_preset_rejected(self):
        p = server.Panel(Mock())
        with self.assertRaises(server.AdbError):
            p.btout_eq("disco")


if __name__ == "__main__":
    unittest.main()
