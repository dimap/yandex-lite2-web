"""Подключение хука к загрузке: команды выполняются настоящим sh во временной папке вместо колонки."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class LocalAdb:
    def __init__(self, root):
        self.root = root

    def shell(self, command, timeout=15, check=True):
        p = subprocess.run(["sh", "-c", command], capture_output=True, text=True)
        return p.stdout, p.stderr, p.returncode

    def push(self, local, remote, timeout=120):
        shutil.copy(local, remote)


def gnu_sed():
    """На колонке sed из busybox (как GNU); BSD sed в macOS понимает -i и «1a» иначе — там этот тест не о чём."""
    try:
        return subprocess.run(["sed", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


@unittest.skipIf(shutil.which("sh") is None or not gnu_sed(), "нужны sh и sed как на колонке (GNU/busybox)")
class RootkeeperHook(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        web, keeper, caller = self.root / "webpanel", self.root / "rootkeeper.sh", self.root / "onboot.sh"
        values = {"WEB_DIR": str(web), "BOOT_HOOK": str(web / "boot_hook.sh"), "ROOTKEEPER": str(keeper),
                  "ROOTKEEPER_CREATED": f"{keeper}.webpanel.created", "ROOTKEEPER_CALLER": str(caller),
                  "BOOT_CUSTOM": str(web / "boot.bin"), "SOUND_CUSTOM": str(web / "s"), "BOOT_ALWAYS": str(web / "a"),
                  "YAM_AUTOSTART": str(web / "y"), "ALARM_CONF": str(web / "al"), "HOSTS_CUSTOM": str(web / "h"),
                  "ASSIST_CONF": str(web / "as"), "INITD_DIR": str(self.root / "init.d"),
                  "INITD_HOOK": str(self.root / "init.d" / "S99zwebpanel")}
        values["ROOTKEEPER_DROP_CREATED"] = server.ROOTKEEPER_DROP_CREATED.replace(server.ROOTKEEPER, str(keeper))
        for name, value in values.items():
            p = patch.object(server, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.keeper, self.caller = keeper, caller
        self.initd = self.root / "init.d"
        self.initd.mkdir()
        self.initd.chmod(0o555)          # как на большинстве колонок: корень только для чтения
        self.addCleanup(self.initd.chmod, 0o755)
        self.panel = server.Panel(LocalAdb(self.root))

    def test_missing_rootkeeper_is_created_when_boot_calls_it_and_removed_on_rollback(self):
        self.caller.write_text(f"[ -x {self.keeper} ] && {self.keeper} &\n")
        self.panel._ensure_hook()
        text = self.keeper.read_text()
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn(server.HOOK_MARK, text)
        self.assertTrue(Path(server.ROOTKEEPER_CREATED).exists())
        self.panel._drop_hook_if_unused()
        self.assertFalse(self.keeper.exists())
        self.assertFalse(Path(server.ROOTKEEPER_CREATED).exists())

    def test_not_created_when_boot_does_not_call_it(self):
        self.caller.write_text("echo nothing\n")
        with self.assertRaises(server.AdbError):
            self.panel._ensure_hook()
        self.assertFalse(self.keeper.exists())

    def test_writable_initd_gets_own_script_when_no_rootkeeper(self):
        # колонка рутована иначе (alicebridge, dropbear): rootkeeper.sh нет, но /etc/init.d можно писать
        self.caller.write_text("echo nothing\n")
        self.initd.chmod(0o755)
        (self.initd / "S99zalicebridge-ssh").write_text("#!/bin/sh\n")
        self.panel._ensure_hook()
        self.panel._ensure_hook()                      # повторно — не дублирует
        script = self.initd / "S99zwebpanel"
        self.assertEqual(script.read_text(), server.initd_hook_script())
        self.assertFalse(self.keeper.exists())
        Path(server.BOOT_HOOK).write_text("#!/bin/sh\necho booted $1 > " + str(self.root / "ran") + "\n")
        Path(server.BOOT_HOOK).chmod(0o755)
        subprocess.run(["sh", str(script), "stop"], check=True)
        self.assertFalse((self.root / "ran").exists())      # rcK зовёт со stop — ничего не запускаем
        subprocess.run(["sh", str(script), "start"], check=True)
        self.assertTrue((self.root / "ran").exists())
        st = self.panel.hook_status()
        self.assertTrue(st["hook"] and st["possible"])
        self.assertEqual(st["file"], str(script))
        self.panel._drop_hook_if_unused()
        self.assertFalse(script.exists())
        self.assertTrue((self.initd / "S99zalicebridge-ssh").exists())

    def test_rootkeeper_wins_over_initd(self):
        self.caller.write_text(f"[ -x {self.keeper} ] && {self.keeper} &\n")
        self.initd.chmod(0o755)
        self.panel._ensure_hook()
        self.assertIn(server.HOOK_MARK, self.keeper.read_text())
        self.assertFalse((self.initd / "S99zwebpanel").exists())

    def test_existing_rootkeeper_keeps_its_own_lines(self):
        self.keeper.write_text("#!/bin/sh\nstart_root_things\n")
        self.panel._ensure_hook()
        self.panel._drop_hook_if_unused()
        self.assertEqual(self.keeper.read_text(), "#!/bin/sh\nstart_root_things\n")


if __name__ == "__main__":
    unittest.main()
