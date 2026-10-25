from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import server


def test_disabled_operations_are_not_routed_even_for_authenticated_requests():
    for method, path in (
        ("do_GET", "/api/term"),
        ("do_POST", "/api/system"),
        ("do_POST", "/api/fs/write"),
        ("do_POST", "/api/yam/install"),
        ("do_POST", "/api/conn/retry"),
        ("do_GET", "/api/devices"),
        ("do_POST", "/api/clock/boot"),
        ("do_POST", "/api/alarms/install"),
        ("do_POST", "/api/backup/restore"),
    ):
        request = SimpleNamespace(path=path, _send=Mock())
        getattr(server.Handler, method)(request)
        request._send.assert_called_once_with(404, {"error": "not found"})


def test_station_control_routes_remain_available():
    for path in ("/api/status", "/api/display", "/api/ring", "/api/music/cmd", "/api/metrics"):
        assert not server.disabled_api_path(urlparse(path).path)
