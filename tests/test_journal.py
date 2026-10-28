import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class Redact(unittest.TestCase):
    def test_hides_secrets_and_addresses(self):
        text = server.redact("sk-proj-abcdefghijklmnop y0_AgAAAAABCDEFGHIJKLMNOP token=abcdefghijklmnopqrstu "
                             "192.168.2.115:5555 aa:bb:cc:dd:ee:ff me@mail.ru /home/dimap/x C:\\Users\\Dima\\y")
        for secret in ("abcdefghijklmnop", "ABCDEFGHIJKLMNOP", "abcdefghijklmnopqrstu", "192.168", "aa:bb", "me@", "dimap", "Dima"):
            self.assertNotIn(secret, text)

    def test_keeps_versions(self):
        for keep in ("0.369.1.50.4794654278.20261021.133", "127.0.0.1:8765", "adb 1.0.41"):
            self.assertEqual(server.redact(keep), keep)


class JournalFile(unittest.TestCase):
    def test_repeats_collapse_and_survive_restart(self):
        path = Path(tempfile.mkdtemp()) / "j.jsonl"
        j = server.Journal(); j.open(path)
        for _ in range(3):
            j.add("связь: потеряна", "warn")
        j.add("POST /api/x — ок")
        self.assertIn("(×3)", j.text())
        again = server.Journal(); again.open(path)
        self.assertEqual([i["msg"] for i in again.items], ["связь: потеряна", "POST /api/x — ок"])

    def test_trims_to_limit(self):
        path = Path(tempfile.mkdtemp()) / "j.jsonl"
        j = server.Journal(); j.LIMIT = 50; j.open(path)
        for n in range(450):
            j.add(f"событие {n}")
        self.assertLessEqual(len(path.read_text().splitlines()), 50 + 200)
        self.assertEqual(j.items[-1]["msg"], "событие 449")


class RequestJournal(unittest.TestCase):
    def handler(self, method, path, data=None, headers=None):
        h = SimpleNamespace(command=method, path=path, _jdata=data or {}, headers=headers or {}, JOURNAL_QUIET=server.Handler.JOURNAL_QUIET,
                            JOURNAL_KEYS=server.Handler.JOURNAL_KEYS)
        return lambda **kw: server.Handler._journal(h, **kw)

    def setUp(self):
        server.JOURNAL.items.clear()

    def test_actions_without_secrets(self):
        self.handler("POST", "/api/assistant", {"api_key": "sk-secret-123456789", "name": "x"})()
        self.handler("POST", "/api/volume", {"level": 5})()          # ползунок — не пишем
        self.handler("GET", "/api/status")()                          # опрос — не пишем
        self.handler("GET", "/api/status")(error="Колонка не ответила")
        msgs = [i["msg"] for i in server.JOURNAL.items]
        self.assertEqual(msgs, ["POST /api/assistant (name=x) — ок", "GET /api/status: Колонка не ответила"])
        self.assertNotIn("sk-secret", " ".join(msgs))


if __name__ == "__main__":
    unittest.main()


class SpeakerFinder(unittest.TestCase):
    """Поиск колонки из панели: адрес запоминается, панель перезапускается с ним."""

    def test_restart_replaces_device_args(self):
        from unittest.mock import patch
        cases = {("server.py", "--open", "--device", "1.2.3.4", "--port", "1"): ["server.py", "--port", "1"],
                 ("server.py", "--find", "--port", "1"): ["server.py", "--port", "1"],
                 ("server.py", "--find", "10.0.0.0/24", "--device=x"): ["server.py"]}
        for argv, rest in cases.items():
            with patch.object(server.sys, "argv", list(argv)), patch.object(server.os, "execv") as ex, \
                    patch.object(server.Handler, "panel", None, create=True):
                server.restart_self(device="192.168.1.50:5555")
            self.assertEqual(ex.call_args.args[1][1:], rest + ["--device", "192.168.1.50:5555"])

    def test_use_accepts_only_home_ip(self):
        from unittest.mock import patch
        h = SimpleNamespace()
        for bad in ("8.8.8.8", "evil.com", "abc", "", None, "192.168.1.5; reboot"):
            with self.assertRaises(server.AdbError):
                server.Handler._conn_use(h, bad)
        with patch.object(server, "save_settings") as save, patch.object(server, "load_settings", return_value={}), \
                patch.object(server.threading, "Timer") as timer:
            self.assertEqual(server.Handler._conn_use(h, "192.168.1.50")["serial"], "192.168.1.50:5555")
        save.assert_called_once_with({"device": "192.168.1.50:5555"})
        self.assertEqual(timer.call_args.kwargs["kwargs"], {"device": "192.168.1.50:5555"})


class SayOnSpeaker(unittest.TestCase):
    """«Сказать на колонке»: текст уходит файлом, в команду оболочки не попадает."""

    def test_alice_voice(self):
        p = server.Panel(Mock())
        p.detach = Mock()
        pushed = []
        p.adb.push = lambda local, remote, *a, **k: pushed.append((Path(local).read_text(encoding="utf-8"), remote))
        p.say("ужин; rm -rf / $(id)\nвторая строка")
        self.assertEqual(pushed, [("Повтори за мной: ужин; rm -rf / $(id) вторая строка\n", "/tmp/webpanel_say.txt")])
        script = p.detach.call_args.args[1]
        self.assertNotIn("rm -rf", script)
        self.assertIn("maind --cli", script)

    def test_assistant_voice_and_limits(self):
        p = server.Panel(Mock())
        p.assistant_command = Mock(return_value={"ok": True})
        p.say("привет", "assistant")
        p.assistant_command.assert_called_once_with({"say": "привет"})
        for bad in ("", "   ", "x" * 301):
            with self.assertRaises(server.AdbError):
                p.say(bad)


class Bluez(unittest.TestCase):
    """Bluetooth — одним запросом по D-Bus: bluetoothctl на каждый опрос раздувал bluetoothd прошивки."""

    SAMPLE = '''method return time=1 sender=:1.1 -> destination=:1.2 serial=3 reply_serial=2
   array [
      dict entry(
         object path "/org/bluez/hci0"
         array [
            dict entry(
               string "org.bluez.Adapter1"
               array [
                  dict entry(
                     string "Alias"
                     variant                         string "Колонка"
                  )
                  dict entry(
                     string "Powered"
                     variant                         boolean true
                  )
                  dict entry(
                     string "Discoverable"
                     variant                         boolean false
                  )
               ]
            )
         ]
      )
      dict entry(
         object path "/org/bluez/hci0/dev_00_11_22_33_44_55"
         array [
            dict entry(
               string "org.bluez.Device1"
               array [
                  dict entry(
                     string "Address"
                     variant                         string "00:11:22:33:44:55"
                  )
                  dict entry(
                     string "Alias"
                     variant                         string "Телефон "в кармане""
                  )
                  dict entry(
                     string "Connected"
                     variant                         boolean true
                  )
               ]
            )
            dict entry(
               string "org.bluez.Battery1"
               array [
                  dict entry(
                     string "Percentage"
                     variant                         byte 87
                  )
               ]
            )
         ]
      )
   ]
'''

    def test_parse(self):
        o = server.parse_bluez(self.SAMPLE)
        self.assertEqual(o["/org/bluez/hci0"]["org.bluez.Adapter1"], {"Alias": "Колонка", "Powered": True, "Discoverable": False})
        dev = o["/org/bluez/hci0/dev_00_11_22_33_44_55"]
        self.assertEqual(dev["org.bluez.Device1"]["Alias"], 'Телефон "в кармане"')
        self.assertTrue(dev["org.bluez.Device1"]["Connected"])
        self.assertEqual(dev["org.bluez.Battery1"]["Percentage"], 87)

    def test_status_and_widget_do_not_run_bluetoothctl(self):
        p = server.Panel(Mock())
        calls = []
        p.adb.shell = lambda cmd, *a, **k: (calls.append(cmd), (self.SAMPLE + "\n@@PAIR\n", "", 0))[1]
        st = p.bt_state()
        self.assertEqual(st["devices"][0]["battery"], 87)
        self.assertTrue(st["devices"][0]["connected"] and st["powered"])
        p.version = lambda: {}
        p.adb.shell = lambda cmd, *a, **k: (calls.append(cmd), ("hostname=x\n@@BLUEZ\n" + self.SAMPLE, "", 0))[1]
        s = p.status()
        self.assertEqual((s["powered"], s["discoverable"]), ("yes", "no"))
        self.assertEqual(s["bt_connected"], ['00:11:22:33:44:55 Телефон "в кармане"'])
        self.assertFalse(any("bluetoothctl" in c for c in calls))
