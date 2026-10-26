"""Install and configure the autonomous speaker service over authenticated ADB.

No API keys in command arguments, local persistent files or returned status.
The daemon's control socket is root-only and has no TCP listener.
"""
import hashlib
import gzip
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import urllib.request

ROOT = Path(__file__).resolve().parent
BASE = "/data/quasar/webpanel/assistant"
BIN = BASE + "/station-assistant"
INIT = "/etc/init.d/S99zwebpanel-assistant"
BUILD_LOCK = threading.Lock()
ENTWARE = "https://bin.entware.net/aarch64-k3.10/"


def vpn_packages(installed):
    """Resolve the fixed OpenVPN package from Entware's HTTPS/SHA256 index."""
    with urllib.request.urlopen(ENTWARE + "Packages.gz", timeout=30) as response:
        index = gzip.decompress(response.read(4 * 1024 * 1024)).decode()
    packages = {}
    for block in index.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line[0].isspace())
        if "Package" in fields:
            packages[fields["Package"]] = fields
    selected, visiting = {}, set()

    def add(name):
        if name in installed or name in selected:
            return
        if name in visiting or name not in packages:
            raise ValueError("Не удалось разрешить зависимости Entware: " + name)
        visiting.add(name)
        p = packages[name]
        for dep in p.get("Depends", "").split(","):
            choices = [part.strip().split(" ")[0] for part in dep.split("|") if part.strip()]
            if choices:
                choice = next((n for n in choices if n in installed),
                              next((n for n in choices if n in packages), choices[0]))
                add(choice)
        visiting.remove(name)
        filename, sha = p.get("Filename", ""), p.get("SHA256sum", "")
        if not re.fullmatch(r"[A-Za-z0-9_.+~-]+\.ipk", filename) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError("Некорректные метаданные пакета Entware")
        selected[name] = p

    add("openvpn-openssl")
    add("ip-full")
    return list(selected.values())


def build_binary():
    """Build once per source digest; never download or execute a remote installer."""
    packaged = ROOT / "device/assistant/station-assistant-linux-arm64"
    if packaged.is_file():
        return packaged
    source = ROOT / "device/assistant"
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(source.glob("*.go"))) +
                            (source / "go.mod").read_bytes()).hexdigest()[:16]
    target = ROOT / "runtime/assistant" / digest / "station-assistant-linux-arm64"
    with BUILD_LOCK:
        if target.is_file():
            return target
        go = shutil.which("go")
        local = ROOT / "runtime/toolchain/go/bin/go"
        if not go and local.is_file():
            go = str(local)
        if not go:
            raise ValueError("Для первой сборки установите Go 1.23+ и перезапустите панель")
        target.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": "arm64",
               "GOTOOLCHAIN": "local", "GOCACHE": str(ROOT / "runtime/go-cache"),
               "GOMODCACHE": str(ROOT / "runtime/go-mod"), "GOPROXY": "off"}
        result = subprocess.run([go, "build", "-trimpath", "-ldflags=-s -w", "-o", str(target), "."],
                                cwd=source, env=env, capture_output=True, timeout=240)
        if result.returncode:
            raise ValueError("Не удалось собрать сервис: " + result.stderr.decode("utf-8", "replace")[:2000])
        return target


class Assistant:
    def __init__(self, adb):
        self.adb = adb
        self.lock = threading.RLock()

    def shell(self, cmd, timeout=30):
        # Old adbd reports exit=0 even when a remote command fails. A final marker
        # carries the actual status; nested sh keeps `set -e` and traps contained.
        marker = "__assistant_exit_" + uuid.uuid4().hex + "__"
        wrapped = "sh -c " + shlex.quote(cmd) + f"; rc=$?; printf '\\n{marker}%s\\n' \"$rc\""
        out, err, rc = self.adb.shell(wrapped, timeout=timeout, check=False)
        output, sep, status = out.rpartition(marker)
        if not rc and sep and status.strip().isdigit():
            rc = int(status.strip())
            out = output.rstrip()
        elif not rc:
            raise ValueError("Колонка не подтвердила выполнение команды")
        if rc:
            raise ValueError("Операция на колонке не завершена: " + (err or out)[-800:].strip())
        return out

    def status(self):
        out = self.shell(f"if [ -x {BIN} ]; then {BIN} ctl status || true; else echo '{{\"installed\":false}}'; fi")
        try:
            result = json.loads(out)
        except ValueError:
            raise ValueError("Сервис вернул некорректное состояние") from None
        if "error" in result and "installed" not in result:
            return {"installed": True, "running": False, "error": result["error"]}
        return result

    def command(self, action, data=None):
        if action not in ("config", "vpn", "test", "stop"):
            raise ValueError("Неизвестная команда помощника")
        raw = json.dumps(data or {}, ensure_ascii=False).encode()
        if len(raw) > 300 * 1024:
            raise ValueError("Слишком большой запрос")
        # ADB's old shell protocol changes binary stdin. Push into a private directory
        # instead; the secret never becomes a process argument or shell interpolation.
        with self.lock, tempfile.TemporaryDirectory() as td:
            local = Path(td) / "request"
            local.touch(mode=0o600)
            local.write_bytes(raw)
            remote = BASE + "/.request-" + uuid.uuid4().hex
            try:
                self.adb.push(local, remote)
                out = self.shell(f"chmod 600 {remote} && {BIN} ctl {action} < {remote}")
            finally:
                self.adb.shell(f"rm -f {remote}", check=False)
            result = json.loads(out)
            if "error" in result and "installed" not in result:
                raise ValueError(result["error"])
            return result

    def _init(self, install):
        # Preserve the mount mode and every other startup entry. No rootkeeper
        # assumptions: this firmware boots through BusyBox /etc/init.d/rcS.
        action = (f"cp {BASE}/init.new {INIT} && chmod 755 {INIT}" if install
                  else f"rm -f {INIT}")
        self.shell("set -e; mode=$(awk '$2 == \"/\" {print $4}' /proc/mounts); "
                   "case \",$mode,\" in *,ro,*) mount -o remount,rw /; "
                   "trap 'mount -o remount,ro /' EXIT;; esac; " + action)

    def install(self):
        with self.lock:
            arch = self.shell("uname -m").strip()
            if arch != "aarch64":
                raise ValueError("Автономный помощник пока поддерживает только aarch64")
            self.shell("test -f /etc/init.d/rcS && test -x /usr/bin/aplay && "
                       "test -x /system/vendor/quasar/maind && test -d /data/quasar/daemons_logs")
            binary = build_binary()
            expected = hashlib.sha256(binary.read_bytes()).hexdigest()
            staged = self.shell(f"if [ -f {BIN}.new ]; then sha256sum {BIN}.new | cut -d ' ' -f 1; fi").strip()
            free = self.shell("df -Pk /data | tail -1 | awk '{print $4}'").strip()
            needed = (0 if staged == expected else binary.stat().st_size) + 8*1024*1024
            if not free.isdigit() or int(free)*1024 < needed:
                raise ValueError("На колонке нужно место для сервиса и ещё 8 МБ свободного запаса")
            self.shell(f"umask 077; mkdir -p {BASE}; chmod 700 {BASE}")
            if staged != expected:
                self.adb.push(binary, BIN + ".new")
            self.shell(f"test \"$(sha256sum {BIN}.new | cut -d ' ' -f 1)\" = {expected} && "
                       f"chmod 700 {BIN}.new && {BIN}.new version")
            # Stop the old executable only after the replacement has been verified.
            self.shell(f"if [ -x {BASE}/service.sh ]; then {BASE}/service.sh stop; fi")
            self.adb.push(ROOT / "device/assistant/service.sh", BASE + "/service.sh")
            with tempfile.TemporaryDirectory() as td:
                init = Path(td) / "init"
                init.write_text(f'#!/bin/sh\nexec {BASE}/service.sh "${{1:-start}}"\n')
                self.adb.push(init, BASE + "/init.new")
            self.shell(f"mv {BIN}.new {BIN}; chmod 700 {BIN} {BASE}/service.sh")
            self._init(True)
            self.shell(f"rm -f {BASE}/init.new; {BASE}/service.sh start")
            for _ in range(20):
                time.sleep(.25)
                try:
                    state = self.status()
                    if state.get("running"):
                        return state
                except ValueError:
                    pass
            raise ValueError("Сервис установлен, но не запустился; проверьте совместимость прошивки")

    def install_vpn(self):
        with self.lock:
            self.shell(f"test -d {BASE} && test -x /opt/bin/opkg && test -c /dev/net/tun && "
                       "/opt/bin/opkg print-architecture | grep -q aarch64-3.10")
            installed = {line.split()[0] for line in self.shell("/opt/bin/opkg list-installed").splitlines() if line}
            packages = vpn_packages(installed)
            size = sum(int(p["Size"]) + int(p.get("Installed-Size", 0)) for p in packages)
            free = int(self.shell("df -Pk /data | tail -1 | awk '{print $4}'").strip()) * 1024
            if free < size + 8 * 1024 * 1024:
                raise ValueError("Для OpenVPN недостаточно свободного места на колонке")
            remote = BASE + "/.packages"
            try:
                with tempfile.TemporaryDirectory() as td:
                    # Verify every package before touching the device's package database.
                    for p in packages:
                        with urllib.request.urlopen(ENTWARE + p["Filename"], timeout=60) as response:
                            blob = response.read(int(p["Size"]) + 1)
                        if len(blob) != int(p["Size"]) or hashlib.sha256(blob).hexdigest() != p["SHA256sum"]:
                            raise ValueError("Контрольная сумма пакета Entware не совпала")
                        (Path(td) / p["Filename"]).write_bytes(blob)
                    if packages:
                        self.shell(f"umask 077; mkdir -p {remote}")
                        for p in packages:
                            self.adb.push(Path(td) / p["Filename"], remote + "/" + p["Filename"])
                        # Old firmware's wget has no TLS. All packages are local here;
                        # remove repository lines so opkg cannot fall back to HTTP.
                        self.shell(f"sed '/^src/d' /opt/etc/opkg.conf > {remote}/opkg.conf && "
                                   f"/opt/bin/opkg -f {remote}/opkg.conf install " +
                                   " ".join(shlex.quote(remote + "/" + p["Filename"]) for p in packages), timeout=120)
                self.shell(f"/opt/sbin/openvpn --version >/dev/null && /opt/sbin/ip -V | grep -q iproute2 && touch {BASE}/.vpn-ready")
            finally:
                self.adb.shell(f"rm -rf {remote}; rm -f {BASE}/opkg.conf", check=False)
            return self.status()

    def uninstall(self):
        with self.lock:
            self.shell(f"if [ -x {BASE}/service.sh ]; then {BASE}/service.sh stop; fi")
            self._init(False)
            self.shell(f"rm -rf {BASE}; rm -f /tmp/webpanel-assistant-supervisor.pid")
            return {"installed": False}
