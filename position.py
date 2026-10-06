"""Playback-position source: answers exactly one question - "what second is it playing right now?".

Public interface:
    get_position() -> float | None   current playback seconds; None when unavailable (player off/not playing)

main.py picks a source based on config.PLAYER. To add a new player:
write a class like MpvSource (implementing get_position), then add one line in config.py and one in main.py.
"""

import json
import socket
import urllib.request

from config import MPV_SOCKET, VLC_URL, MANUAL_POSITION


class MpvSource:
    """mpv / IINA: reads time-pos via the unix-socket JSON IPC."""

    name = "mpv / IINA"

    def get_position(self):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect(MPV_SOCKET)
                s.sendall(b'{"command": ["get_property", "time-pos"]}\n')
                buf = b""
                while b"\n" not in buf:
                    buf += s.recv(4096)
            reply = json.loads(buf)
            return reply.get("data")  # None while paused or not playing
        except Exception:
            return None


class VlcSource:
    """VLC: reads the time field from the HTTP status endpoint."""

    name = "VLC"

    def get_position(self):
        try:
            with urllib.request.urlopen(VLC_URL.rstrip("/") + "/requests/status.json", timeout=2) as r:
                data = json.loads(r.read())
            return data.get("time")
        except Exception:
            return None


class ManualSource:
    """Manual mode: always returns config.MANUAL_POSITION, so you can try without a player."""

    name = "manual (fixed position, for testing)"

    def get_position(self):
        return MANUAL_POSITION
