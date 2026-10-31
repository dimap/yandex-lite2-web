"""Живой спектр не должен пропадать вместе со светодиодами."""
import time
import unittest
from unittest.mock import Mock

import server


class LedStreamSpectrum(unittest.TestCase):
    def test_spectrum_survives_missing_leds(self):
        s = server.LedStream(Mock())
        s.running = True
        s.viz = {"bands": [0.1, 0.2, 0.3, 0.4], "rms": 0.2, "t": 123}
        s.viz_at = time.time()
        r = s.get()
        self.assertIsNone(r["v"])
        self.assertEqual(r["spectrum"]["bands"], [0.1, 0.2, 0.3, 0.4])

    def test_stale_spectrum_is_dropped(self):
        s = server.LedStream(Mock())
        s.running = True
        s.viz = {"bands": [0.1, 0.2, 0.3, 0.4]}
        s.viz_at = time.time() - 5
        self.assertNotIn("spectrum", s.get())


if __name__ == "__main__":
    unittest.main()
