import http.client
import json
import threading
import tempfile
from collections import deque
from pathlib import Path
from http.server import ThreadingHTTPServer

import access
import sdk_server


class FakeAdapter:
    last_display = {"mode": None}
    station = type("Station", (), {"info": lambda _self: {"ok": True}})()

    def status(self):
        return {"transport": "ssh", "cpu_usage_percent": 12.5, "temp": 39000,
                "mem_total": 1024 * 1024, "mem_avail": 512 * 1024, "RSSI": None, "load": "0.1 0.2 0.3"}

    def display(self, data):
        self.last_display = {"mode": "frame", "ttl": data["ttl"]}
        return self.last_display

    @staticmethod
    def display_channels():
        return ["A0", "B0"]


def test_only_supported_sdk_routes_are_reachable():
    with tempfile.TemporaryDirectory() as directory:
        server = ThreadingHTTPServer(("127.0.0.1", 0), sdk_server.PanelHandler)
        server.allowed_hosts = {f"127.0.0.1:{server.server_port}"}
        sdk_server.PanelHandler.adapter = FakeAdapter()
        sdk_server.PanelHandler.panel_access = access.PanelAccess(Path(directory) / "auth.json")
        sdk_server.PanelHandler.request_windows = {}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            client.request("GET", "/api/status")
            response = client.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["transport"] == "ssh"

            client.request("GET", "/healthz", headers={"Host": f"127.0.0.1:{server.server_port}"})
            response = client.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok"}

            client.request("GET", "/api/display/schema", headers={"Host": f"127.0.0.1:{server.server_port}"})
            response = client.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"channels": ["A0", "B0"], "max_brightness": 0.15}

            for path in ("/api/term", "/api/system", "/api/fs/write", "/api/yam/install", "/api/clock/boot"):
                client.request("POST", path, body="{}", headers={
                    "Host": f"127.0.0.1:{server.server_port}",
                    "Origin": f"http://127.0.0.1:{server.server_port}",
                    "Content-Type": "application/json",
                })
                response = client.getresponse()
                assert response.status == 404
                response.read()
            client.close()
        finally:
            server.shutdown()
            server.server_close()


def test_metrics_contract_reports_real_series_and_unavailable_event_history():
    probe = type("Probe", (), {"adapter": FakeAdapter(), "history": deque(), "next_sample": 0.0})()
    data = sdk_server.PanelHandler._metrics(probe, 900)

    assert data["cpu"] == [12.5]
    assert data["temp"] == [39.0]
    assert data["mem"] == [512.0]
    assert data["totals"]["mem"] == 1024.0
    assert data["play"] == [None]
    assert data["events"] == []
    assert data["play_supported"] is data["events_supported"] is False
    assert data["to"] - data["from"] == 900


def test_network_auth_origin_and_host_guards():
    with tempfile.TemporaryDirectory() as directory:
        auth = access.PanelAccess(Path(directory) / ".panel_auth.json")
        auth.set_password("", "unit-test-password", "unittest")
        server = ThreadingHTTPServer(("127.0.0.1", 0), sdk_server.PanelHandler)
        host = f"127.0.0.1:{server.server_port}"
        server.allowed_hosts = {host}
        sdk_server.PanelHandler.adapter = FakeAdapter()
        sdk_server.PanelHandler.panel_access = auth
        sdk_server.PanelHandler.data_dir = Path(directory)
        sdk_server.PanelHandler.request_windows = {}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            client.request("GET", "/", headers={"Host": host})
            assert client.getresponse().status == 200
            client.request("GET", "/api/status", headers={"Host": host})
            assert client.getresponse().status == 401

            client.request("POST", "/api/panel-auth/login", body=json.dumps({"password": "unit-test-password"}), headers={
                "Host": host, "Origin": f"http://{host}", "Content-Type": "application/json"})
            response = client.getresponse()
            cookie = response.getheader("Set-Cookie")
            assert response.status == 200 and "HttpOnly" in cookie and "SameSite=Strict" in cookie
            cookie = cookie.split(";", 1)[0]
            client.request("GET", "/api/status", headers={"Host": host, "Cookie": cookie})
            assert client.getresponse().status == 200

            client.request("POST", "/api/conn/retry", body="{}", headers={
                "Host": host, "Origin": "https://evil.invalid", "Cookie": cookie, "Content-Type": "application/json"})
            assert client.getresponse().status == 403
            client.request("POST", "/api/fs/write", body="{}", headers={
                "Host": host, "Origin": f"http://{host}", "Cookie": cookie, "Content-Type": "application/json"})
            assert client.getresponse().status == 404
            client.request("GET", "/api/status", headers={"Host": "attacker.invalid", "Cookie": cookie})
            assert client.getresponse().status == 400
        finally:
            client.close()
            server.shutdown()
            server.server_close()
