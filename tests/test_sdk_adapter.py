from sdk_adapter import StationAdapter


class Credentials:
    def status(self):
        return {"authenticated": False}


class Station:
    def __init__(self):
        self.device = type("Device", (), {"alias": "station-lite2"})()
        self.calls = []
        self.display = type("Display", (), {
            "frame": lambda _self, values, ttl: self._call("display", tuple(values), ttl),
        })()
        self.led = type("Led", (), {
            "frame": lambda _self, values, brightness, ttl: self._call("ring", values, brightness, ttl),
        })()
        self.media = type("Media", (), {"status": lambda _self: {"available": False}})()

    def _call(self, *args):
        self.calls.append(args)
        return {"ok": True}

    def capabilities(self):
        return {"device": {}, "station": {}}


def test_raw_display_frames_and_rgb_use_sdk_ttl_operations():
    station = Station()
    adapter = StationAdapter(station, Credentials())

    result = adapter.display({"v": list(range(36)), "ttl": 4})
    adapter.ring({"red": 1, "green": 2, "blue": 3, "brightness": 0.05, "ttl": 6})

    assert result["mode"] == "frame"
    assert station.calls == [
        ("display", tuple(round(value * 0.1) for value in range(36)), 4),
        ("ring", ((1, 2, 3),) * 3, 0.05, 6),
    ]


def test_display_accepts_canonical_channel_map_and_rejects_unsafe_values():
    station = Station()
    adapter = StationAdapter(station, Credentials())
    values = {name: 255 if name == "H0" else 0 for name in adapter.display_channels()}

    adapter.display({"v": values, "brightness": 0.15, "ttl": 1})
    assert station.calls == [("display", tuple(38 if name == "H0" else 0 for name in adapter.display_channels()), 1)]
    for data in ({"v": [256] * 36}, {"v": [0] * 35}, {"v": [0] * 36, "ttl": 31},
                 {"v": [0] * 36, "brightness": 0.16}):
        try:
            adapter.display(data)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid frame {data!r}")


def test_media_status_reports_unavailable_without_claiming_live_events():
    station = Station()
    adapter = StationAdapter(station, Credentials())

    status = adapter.media()

    assert status["glagol"]["connected"] is False
    assert adapter.capabilities()["events"]["live_subscription"] is False
