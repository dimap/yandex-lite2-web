"""Translate the panel's small supported surface to the Station SDK."""

from __future__ import annotations

import math
import re
import time
from typing import Any

from plum_sdk import Station
from plum_sdk.auth import CredentialsStore
from plum_sdk.render import DISPLAY_CHANNELS


class StationAdapter:
    def __init__(self, station: Station | None = None, credentials: CredentialsStore | None = None):
        self.station = station or Station()
        self.credentials = credentials or CredentialsStore()
        self.last_display: dict[str, Any] = {"mode": None}
        self._status_at = 0.0
        self._status = None
        self._media_at = 0.0
        self._media = None

    def capabilities(self) -> dict[str, Any]:
        values = self.station.capabilities()
        values["display"] = {
            "direct_frames": True, "native_animation": False,
            "native_ownership": False, "snapshot": False,
        }
        values["ring"] = {"rgb": True, "snapshot": False}
        values["events"] = {"live_subscription": False}
        return values

    def status(self) -> dict[str, Any]:
        if self._status is not None and time.monotonic() - self._status_at < 15:
            return self._status.copy()
        info = self.station.info()
        resources = self.station.resources()
        network = self.station.network()
        health = self.station.health()
        try:
            temperature = self.station.temperature()["millidegrees_c"]
        except Exception:
            temperature = None

        memory = info.get("memory", {})
        load = resources.get("load_average", [])
        addresses = "\n".join(network.get("addresses", []))
        link = "\n".join(network.get("wifi_link", []))
        details = health.get("details", health)
        processes = "\n".join(details.get("processes", []))
        address_matches = re.findall(r"inet (\d+(?:\.\d+){3})/", addresses)
        ip_address = next((address for address in address_matches
                           if not address.startswith(("127.", "169.254."))),
                          address_matches[0] if address_matches else None)
        rssi_match = re.search(r"signal:\s*(-?\d+)\s*dBm", link)
        freq_match = re.search(r"freq:\s*(\d+)", link)
        firmware = info.get("firmware", {})

        self._status = {
            "hostname": getattr(self.station.device, "alias", "station-lite2"),
            "hardware_model": "Yandex Plum Lite 2",
            "ip_address": ip_address,
            "firmware": firmware.get("version"),
            "kernel": firmware.get("kernel"),
            "uptime": info.get("uptime_seconds"),
            "cpu_usage_percent": resources.get("cpu_usage_percent"),
            "load": " ".join(str(value) for value in load),
            "mem_total": memory.get("MemTotal_kB"),
            "mem_avail": memory.get("MemAvailable_kB"),
            "temp": temperature,
            "RSSI": rssi_match.group(1) if rssi_match else None,
            "freq": freq_match.group(1) if freq_match else None,
            "quasar": "running" if "maind" in processes else "stopped" if health.get("available") else "unknown",
            "vol_level": None,
            "vol_bt": None,
            "capabilities": self.capabilities(),
        }
        self._status_at = time.monotonic()
        return self._status.copy()

    def media(self) -> dict[str, Any]:
        if self._media is not None and time.monotonic() - self._media_at < 2:
            return self._media.copy()
        account = self.credentials.status()
        try:
            status = self.station.media.status()
        except Exception as error:
            status = {"available": False, "error": str(error)}

        player = {}
        for source, target in (
            ("player_type", "playerType"), ("title", "title"), ("artist", "subtitle"),
            ("progress", "progress"), ("duration", "duration"),
            ("has_next", "hasNext"), ("has_prev", "hasPrev"),
            ("has_pause", "hasPause"), ("has_play", "hasPlay"),
            ("has_progress_bar", "hasProgressBar"),
        ):
            if source in status:
                player[target] = status[source]

        state = {"playerState": player, "playing": status.get("playing"),
                 "volume": status.get("volume")}
        self._media = {
            "auth": {"logged_in": bool(account.get("authenticated")), "login": account.get("login")},
            "glagol": {"connected": status.get("available", False), "state": state,
                       "updated": time.time(), "error": status.get("error")},
        }
        self._media_at = time.monotonic()
        return self._media.copy()

    def display(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("mode", "frame") != "frame":
            raise ValueError("only temporary static frames are supported")
        ttl = data.get("ttl", 5)
        brightness = data.get("brightness", 0.1)
        if type(ttl) is not int or not 1 <= ttl <= 30:
            raise ValueError("display TTL must be an integer from 1 to 30 seconds")
        if isinstance(brightness, bool) or not isinstance(brightness, (int, float)) or not math.isfinite(brightness) or not 0 < brightness <= 0.15:
            raise ValueError("display brightness must be greater than 0 and no more than 0.15")
        values = data.get("v", ())
        if isinstance(values, dict):
            if set(values) != set(DISPLAY_CHANNELS):
                raise ValueError("raw frame must name all 36 canonical display channels")
            values = tuple(values[name] for name in DISPLAY_CHANNELS)
        if not isinstance(values, (list, tuple)) or len(values) != len(DISPLAY_CHANNELS) or any(type(v) is not int or not 0 <= v <= 255 for v in values):
            raise ValueError("display frame must contain 36 integer brightness values in 0..255")
        frame = tuple(round(value * brightness) for value in values)
        result = self.station.display.frame(frame, ttl=ttl)
        self.last_display = {**result, "mode": "frame", "ttl": ttl, "brightness": brightness}
        return self.last_display

    @staticmethod
    def display_channels() -> list[str]:
        return list(DISPLAY_CHANNELS)

    def ring(self, data: dict[str, Any]) -> dict[str, Any]:
        ttl = data.get("ttl", 5)
        brightness = data.get("brightness", 0.1)
        if type(ttl) is not int or not 1 <= ttl <= 30:
            raise ValueError("ring TTL must be an integer from 1 to 30 seconds")
        if isinstance(brightness, bool) or not isinstance(brightness, (int, float)) or not math.isfinite(brightness) or not 0 < brightness <= 0.15:
            raise ValueError("ring brightness must be greater than 0 and no more than 0.15")
        color = tuple(data.get(channel, 0) for channel in ("red", "green", "blue"))
        return self.station.led.frame((color,) * 3, brightness=brightness, ttl=ttl)

    def music_command(self, data: dict[str, Any]) -> dict[str, Any]:
        command = data.get("command")
        if command == "play":
            return self.station.media.play()
        if command == "stop":
            return self.station.media.pause()
        if command in ("next", "prev"):
            return (self.station.media.next if command == "next" else self.station.media.previous)()
        if command == "setVolume":
            return self.station.media.volume(data.get("volume"))
        if command == "sendText":
            return self.station.alice.ask(data.get("text", ""), response="none")
        raise ValueError("unsupported player command")
