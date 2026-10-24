"""A cable straight to a laptop: when a network cable finds no network, the tablet becomes one.

Plugged into a router or the dock at home, the cable gets an address as usual and
nothing here happens. Plugged straight into a laptop, nobody hands out addresses, so
after a short wait the tablet switches that cable to a link-local address
(169.254.x.x), as a Mac or PC does by itself when no router answers. The two find
each other by name, and the NAS opens at smb://<tablet>.local as on any network.

Link-local hands out nothing: unlike the hotspot's shared mode, it can never act
as an address server on someone else's network if it ever switched on there.

When the cable is pulled out, NetworkManager drops the direct connection by itself,
so the next cable starts afresh and a router is always tried first.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import network

DIRECT = "DoubleTake-Direct"   # the direct connection's name in NetworkManager
WAIT = 20.0                      # seconds a cable may look for a network before going direct
RETRY = 60.0                     # after a failed attempt, wait this long before the next


def carrier(device: str, root: str = "/sys/class/net") -> bool:
    """Is a cable plugged in, and live, on this network port?"""
    try:
        return (Path(root) / device / "carrier").read_text().strip() == "1"
    except OSError:
        return False


def _profile_device() -> str:
    code, out, _ = network._run(["-g", "connection.interface-name", "connection", "show", "id", DIRECT], timeout=10)
    return out.strip() if code == 0 else ""


def _make_profile(device: str, zone: str) -> str:
    """The direct connection for one port, made fresh. Returns an error, or ''."""
    for _ in range(3):
        if network._run(["connection", "delete", "id", DIRECT], timeout=15)[0] != 0:
            break
    code, _, err = network._run([
        "connection", "add", "type", "ethernet", "ifname", device, "con-name", DIRECT,
        "ipv4.method", "link-local", "ipv6.method", "link-local",
        "connection.autoconnect", "no", "connection.zone", zone,
    ], timeout=20)
    return "" if code == 0 else (err or "Could not set up the direct cable.")


def _profile_zone() -> str:
    code, out, _ = network._run(["-g", "connection.zone", "connection", "show", "id", DIRECT], timeout=10)
    return out.strip() if code == 0 else ""


def go_direct(device: str, zone: str = "home") -> str:
    """Switch one port to the direct cable. 'zone' is the firewall zone: home when the
    NAS may be seen on a cable, public when not."""
    if _profile_device() != device or _profile_zone() != zone:
        error = _make_profile(device, zone)
        if error:
            return error
    code, _, err = network._run(["connection", "up", "id", DIRECT, "ifname", device], timeout=30)
    return "" if code == 0 else (err or "Could not start the direct cable.")


def stop() -> None:
    network._run(["connection", "down", "id", DIRECT], timeout=15)


class DirectCable:
    """Watches the network ports, every couple of seconds, and goes direct when a cable finds nobody."""

    def __init__(self, clock=time.monotonic, has_carrier=carrier, start=go_direct):
        self.clock, self.has_carrier, self.start = clock, has_carrier, start
        self.since: dict = {}      # port -> when its cable started looking for a network
        self.failed: dict = {}     # port -> when going direct last failed
        self.error = ""

    def tick(self, devices: list, enabled: bool = True, zone: str = "home") -> dict:
        """One look at the ports. Returns what the screens show about the direct cable."""
        now = self.clock()
        state = {"active": False, "device": "", "waiting": False, "error": self.error}
        for d in devices:
            if d.get("type") != "ethernet":
                continue
            port, connected = d["device"], d.get("state", "").startswith("connected")
            if connected and d.get("connection") == DIRECT:
                state.update(active=True, device=port)
                self.since.pop(port, None)
                continue
            if connected or not self.has_carrier(port):
                self.since.pop(port, None)  # on a network, or no cable: nothing to do
                continue
            if not enabled:
                continue
            first = self.since.setdefault(port, now)
            if now - self.failed.get(port, -1e9) < RETRY:
                continue
            if now - first < WAIT:
                state["waiting"] = True
                continue
            error = self.start(port, zone)
            if error:
                self.failed[port] = now
                self.error = error
            else:
                self.error = ""
                self.since.pop(port, None)
        state["error"] = self.error
        return state
