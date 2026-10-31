"""Mute: выключение звука с запоминанием громкости."""
import unittest
from unittest.mock import Mock, patch

import server


class Mute(unittest.TestCase):
    def setUp(self):
        self.panel = server.Panel(Mock())
        self.panel.glagol = Mock()
        self.panel.glagol.ws = True
        self.panel.glagol.state = {"volume": 0.7}
        self.saved = {}
        for target, effect in (
                ("server.load_settings", lambda: dict(self.saved)),
                ("server.save_settings", lambda d: (self.saved.clear(), self.saved.update(d)))):
            p = patch(target, side_effect=effect)
            p.start()
            self.addCleanup(p.stop)

    def test_level_comes_from_glagol(self):
        self.assertEqual(self.panel._volume_level(), 70)

    def test_mute_remembers_and_restores(self):
        self.panel.set_volume = Mock()
        self.assertEqual(self.panel.set_mute(True), {"muted": True, "volume": 0})
        self.assertAlmostEqual(self.saved["mute_before"], 0.7)
        self.panel.set_volume.assert_called_with({"level": 0})
        self.assertEqual(self.panel.set_mute(False), {"muted": False, "volume": 70})
        self.panel.set_volume.assert_called_with({"level": 70})
        self.assertNotIn("mute_before", self.saved)

    def test_new_volume_unmutes(self):
        self.saved["mute_before"] = 0.7
        self.panel.glagol.send = Mock()
        self.panel.set_volume({"level": 30})
        self.assertNotIn("mute_before", self.saved)
        self.panel.glagol.send.assert_called_once_with({"command": "setVolume", "volume": 0.3})


if __name__ == "__main__":
    unittest.main()
