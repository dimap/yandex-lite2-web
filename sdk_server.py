"""Small authenticated HTTP adapter for the supported Station SDK operations."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import ssl
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import access
from plum_sdk.errors import AuthenticationError, DeviceOffline, InvalidArgument, OperationTimeout, StationError, UnsupportedCapability

from sdk_adapter import StationAdapter


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
MIME = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml",
        ".webmanifest": "application/manifest+json", ".png": "image/png"}
MAX_BODY = 1_048_576


class PanelHandler(BaseHTTPRequestHandler):
    adapter: StationAdapter
    panel_access: access.PanelAccess
    data_dir: Path
    https_port = 0
    history = deque(maxlen=2880)
    next_sample = 0.0
    request_windows = {}
    request_lock = threading.Lock()

    def log_message(self, *_args):
        pass

    def _send(self, status: int, body, content_type="application/json; charset=utf-8", headers=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _token(self):
        from http.cookies import SimpleCookie

        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookie.get(access.COOKIE)
        return morsel.value if morsel else None

    def _host_valid(self):
        host = self.headers.get("Host", "").lower()
        if host not in self.server.allowed_hosts:
            self._send(400, {"error": "invalid host"})
            return False
        return True

    def _guard(self, path):
        if not self._host_valid():
            return True
        limit = 120 if self.command == "GET" else 30
        now = time.monotonic()
        with self.request_lock:
            window = self.request_windows.setdefault(self.client_address[0], deque())
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= limit:
                self._send(429, {"error": "rate limit exceeded"}, headers=[("Retry-After", "60")])
                return True
            window.append(now)
        gateway_secret = os.environ.get("PANEL_GATEWAY_SECRET")
        if gateway_secret:
            import hmac

            if self.connection.getsockname()[0] != "127.0.0.1" or not hmac.compare_digest(
                self.headers.get("X-Panel-Gateway", ""), gateway_secret
            ):
                self._send(403, {"error": "private worker"})
                return True
        if self.command == "POST":
            origin = self.headers.get("Origin", "")
            parsed = urlsplit(origin)
            scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
            if parsed.scheme != scheme or parsed.netloc.lower() != self.headers.get("Host", "").lower():
                self._send(403, {"error": "origin rejected"})
                return True
        public = {"/", "/index.html", "/manifest.webmanifest", "/sw.js", "/model.stl",
                  "/healthz", "/api/panel-auth", "/api/panel-auth/login", "/api/display/schema"}
        if path not in public and not path.startswith("/static/") and self.panel_access.enabled and not self.panel_access.session_ok(self._token()):
            self._send(401, {"error": "login required", "auth": True})
            return True
        return False

    def _json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 <= length <= MAX_BODY:
            raise ValueError("request body exceeds limit")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _panel_auth(self, path, data):
        token, user_agent = self._token(), self.headers.get("User-Agent", "")
        try:
            if path == "/api/panel-auth/login":
                new_token = self.panel_access.login(self.client_address[0], data.get("password"), user_agent)
                return self._send(200, {"ok": True}, headers=[self._cookie(new_token)])
            if path == "/api/panel-auth/logout":
                self.panel_access.logout(token)
                return self._send(200, {"ok": True}, headers=[self._cookie(None)])
            if path == "/api/panel-auth/password":
                new_token = self.panel_access.set_password(data.get("current"), data.get("password"), user_agent)
                return self._send(200, {"ok": True}, headers=[self._cookie(new_token)])
            if path == "/api/panel-auth/disable":
                self.panel_access.disable(data.get("current"))
                return self._send(200, {"ok": True}, headers=[self._cookie(None)])
            if path == "/api/panel-auth/logout-others":
                return self._send(200, {"ok": True, "closed": self.panel_access.logout_others(token)})
        except access.AccessError as error:
            return self._send(400, {"error": str(error)})
        return self._send(404, {"error": "not found"})

    def _cookie(self, token):
        attrs = "Path=/; HttpOnly; SameSite=Strict" + ("; Secure" if isinstance(self.connection, ssl.SSLSocket) else "")
        value = token or ""
        age = access.SESSION_TTL if token else 0
        return "Set-Cookie", f"{access.COOKIE}={value}; Max-Age={age}; {attrs}"

    def _metrics(self, seconds):
        now = time.time()
        status = None
        if now >= self.next_sample:
            status = self.adapter.status()
            self.history.append({
                "t": now, "cpu": status.get("cpu_usage_percent"),
                "temp": status["temp"] / 1000 if status["temp"] is not None else None,
                "mem": (status["mem_total"] - status["mem_avail"]) / 1024
                if status["mem_total"] is not None and status["mem_avail"] is not None else None,
                "rssi": float(status["RSSI"]) if status["RSSI"] is not None else None,
                "disk": None, "load": float(status["load"].split()[0]) if status["load"] else None,
            })
            self.next_sample = now + 30
        if status is None:
            status = self.adapter.status()
        samples = [sample for sample in self.history if now - sample["t"] <= seconds]
        return {
            "from": now - seconds, "to": now, "step": 30, "interval": 30,
            "t": [sample["t"] for sample in samples],
            "totals": {"mem": status["mem_total"] / 1024 if status["mem_total"] is not None else None},
            "play": [None] * len(samples), "events": [],
            "play_supported": False, "events_supported": False,
            **{
            key: [sample[key] for sample in samples] for key in ("cpu", "temp", "mem", "rssi", "disk", "load")
            },
        }

    def do_GET(self):
        path = urlsplit(self.path).path
        if self._guard(path):
            return
        try:
            if path == "/api/panel-auth":
                return self._send(200, {**self.panel_access.info(self._token()),
                                        "secure": isinstance(self.connection, ssl.SSLSocket),
                                        "ca": (self.data_dir / ".tls" / "ca.crt").is_file(),
                                        "ports": {"https": self.https_port or None}})
            if path == "/ca.crt":
                return self._send(200, (self.data_dir / ".tls" / "ca.crt").read_bytes(), "application/x-x509-ca-cert",
                                  headers=[("Content-Disposition", 'attachment; filename="station-lite2-panel-ca.crt"')])
            if path == "/healthz":
                return self._send(200, {"status": "ok"})
            if path == "/api/profile":
                return self._send(200, {"id": "lite2", "name": "Станция Лайт 2", "clock": True,
                                        "light": True, "boot": False, "alarms": False,
                                        "light_note": "RGB работает ограниченное время; live snapshot недоступен."})
            if path == "/api/capabilities":
                return self._send(200, self.adapter.capabilities())
            if path == "/api/status":
                return self._send(200, self.adapter.status())
            if path == "/api/conn":
                self.adapter.status()
                return self._send(200, {"state": "online", "transport": "ssh"})
            if path == "/api/music":
                return self._send(200, self.adapter.media())
            if path == "/api/display":
                return self._send(200, self.adapter.last_display)
            if path == "/api/display/schema":
                return self._send(200, {"channels": self.adapter.display_channels(), "max_brightness": 0.15})
            if path == "/api/metrics":
                try:
                    seconds = min(86_400, max(60, int(parse_qs(urlsplit(self.path).query).get("range", [3600])[0])))
                except ValueError:
                    seconds = 3600
                return self._send(200, self._metrics(seconds))
            if path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), MIME[".html"])
            if path == "/manifest.webmanifest":
                return self._send(200, (STATIC / "manifest.webmanifest").read_bytes(), MIME[".webmanifest"])
            if path == "/sw.js":
                return self._send(200, (STATIC / "sw.js").read_bytes(), MIME[".js"])
            if path == "/model.stl":
                model = ROOT / "3d" / "Yandex_Station_Lite_2.stl"
                return self._send(200, model.read_bytes(), "model/stl")
            if path.startswith("/static/"):
                file = (STATIC / unquote(path.removeprefix("/static/"))).resolve()
                if STATIC in file.parents and file.is_file():
                    return self._send(200, file.read_bytes(), MIME.get(file.suffix, "application/octet-stream"))
        except (AuthenticationError, DeviceOffline, OperationTimeout) as error:
            return self._send(503, {"error": str(error), "offline": True})
        except (InvalidArgument, ValueError) as error:
            return self._send(400, {"error": str(error)})
        except UnsupportedCapability as error:
            return self._send(501, {"error": str(error), "supported": False})
        except StationError as error:
            return self._send(502, {"error": str(error)})
        except FileNotFoundError:
            return self._send(404, {"error": "not found"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if self._guard(path):
            return
        try:
            data = self._json()
            if path.startswith("/api/panel-auth/"):
                return self._panel_auth(path, data)
            if path == "/api/display":
                return self._send(200, self.adapter.display(data))
            if path == "/api/ring":
                return self._send(200, self.adapter.ring(data))
            if path == "/api/music/cmd":
                return self._send(200, self.adapter.music_command(data))
            if path == "/api/volume":
                if "level" not in data:
                    raise UnsupportedCapability("Bluetooth volume control")
                level = data["level"]
                if (isinstance(level, bool) or not isinstance(level, (int, float))
                        or not 0 <= level <= 100 or not math.isfinite(level)):
                    raise ValueError("volume level must be between 0 and 100")
                return self._send(200, self.adapter.station.media.volume(level / 100))
            if path == "/api/conn/retry":
                self.adapter.status()
                return self._send(200, {"state": "online", "transport": "ssh"})
            if path == "/api/metrics/clear":
                self.history.clear()
                self.next_sample = 0
                return self._send(200, {"ok": True})
        except (AuthenticationError, DeviceOffline, OperationTimeout) as error:
            return self._send(503, {"error": str(error), "offline": True})
        except (InvalidArgument, ValueError, KeyError) as error:
            return self._send(400, {"error": str(error)})
        except UnsupportedCapability as error:
            return self._send(501, {"error": str(error), "supported": False})
        except StationError as error:
            return self._send(502, {"error": str(error)})
        return self._send(404, {"error": "not found"})


def main():
    parser = argparse.ArgumentParser(description="Station Lite 2 web panel using plum_sdk")
    parser.add_argument("--host", default=os.getenv("PANEL_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PANEL_PORT", "8765")))
    parser.add_argument("--https-port", type=int, default=int(os.getenv("PANEL_HTTPS_PORT", "8766")))
    parser.add_argument("--ssh-alias", default=os.getenv("STATION_SSH_ALIAS", "station-lite2"))
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("PANEL_DATA_DIR", ROOT / "data")))
    args = parser.parse_args()
    if args.port < 0 or args.https_port < 0 or not (args.port or args.https_port):
        parser.error("enable at least one listener and use non-negative ports")
    try:
        bind_address = ipaddress.ip_address(args.host)
    except ValueError:
        bind_address = None
    access_path = args.data_dir / ".panel_auth.json"
    args.data_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(args.data_dir, 0o700)
    panel_access = access.PanelAccess(access_path)
    if not panel_access.enabled and (bind_address is None or not bind_address.is_loopback):
        parser.error("set a panel password on localhost before binding to a network interface")
    os.environ["STATION_SSH_ALIAS"] = args.ssh_alias
    PanelHandler.adapter = StationAdapter()
    PanelHandler.panel_access = panel_access
    PanelHandler.data_dir = args.data_dir
    PanelHandler.history = deque(maxlen=2880)
    PanelHandler.next_sample = 0
    PanelHandler.https_port = args.https_port
    PanelHandler.timeout = 20
    PanelHandler.daemon_threads = True
    servers = []
    if args.port:
        http = ThreadingHTTPServer((args.host, args.port), PanelHandler)
        servers.append(http)
    if args.https_port:
        certificate, key = access.ensure_certs(args.data_dir / ".tls")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certificate, key)
        https = ThreadingHTTPServer((args.host, args.https_port), PanelHandler)
        https.socket = context.wrap_socket(https.socket, server_side=True)
        servers.append(https)
    explicit_hosts = {host.strip().lower() for host in os.getenv("PANEL_ALLOWED_HOSTS", "").split(",") if host.strip()}
    for listener in servers:
        address, port = listener.server_address[:2]
        listener.allowed_hosts = {"localhost", "127.0.0.1", address.lower(), f"{address}:{port}".lower(), *explicit_hosts}
        listener.daemon_threads = True
    if args.port:
        print(f"Station panel listening on http://{args.host}:{http.server_port}", flush=True)
    if args.https_port:
        print(f"Station panel TLS listening on https://{args.host}:{https.server_port}", flush=True)
    try:
        for listener in servers[1:]:
            threading.Thread(target=listener.serve_forever, daemon=True).start()
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for listener in servers:
            listener.server_close()


if __name__ == "__main__":
    main()
