"""Tests for the direct cable: a cable that finds no network links straight to the laptop."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from doubletake import direct, nas  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def port(state="connecting (getting IP configuration)", connection="Wired connection 1"):
    return {"device": "enp0s20u1", "type": "ethernet", "state": state, "connection": connection}


class DirectCableTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.plugged = True
        self.started = []
        self.answer = ""

        def start(device, zone):
            self.started.append((device, zone))
            return self.answer

        self.cable = direct.DirectCable(clock=self.clock, has_carrier=lambda d: self.plugged, start=start)

    def test_goes_direct_only_after_waiting(self):
        self.assertTrue(self.cable.tick([port()])["waiting"])
        self.clock.now += 10
        self.cable.tick([port()])
        self.assertEqual(self.started, [])
        self.clock.now += 11
        self.cable.tick([port()], zone="home")
        self.assertEqual(self.started, [("enp0s20u1", "home")])

    def test_a_router_answering_means_nothing_happens(self):
        self.cable.tick([port()])
        self.clock.now += 5
        self.cable.tick([port("connected", "Wired connection 1")])  # the dock's network answered
        self.clock.now += 60
        self.cable.tick([port("connected", "Wired connection 1")])
        self.assertEqual(self.started, [])

    def test_no_cable_no_direct(self):
        self.plugged = False
        self.cable.tick([port("unavailable")])
        self.clock.now += 100
        self.cable.tick([port("unavailable")])
        self.assertEqual(self.started, [])

    def test_switched_off(self):
        self.cable.tick([port()], enabled=False)
        self.clock.now += 100
        self.cable.tick([port()], enabled=False)
        self.assertEqual(self.started, [])

    def test_reports_when_linked(self):
        state = self.cable.tick([port("connected", direct.DIRECT)])
        self.assertTrue(state["active"])
        self.assertEqual(state["device"], "enp0s20u1")

    def test_a_failure_waits_before_trying_again(self):
        self.answer = "no such device"
        self.cable.tick([port()])
        self.clock.now += 25
        state = self.cable.tick([port()])
        self.assertEqual(len(self.started), 1)
        self.assertIn("no such device", state["error"])
        self.clock.now += 30
        self.cable.tick([port()])
        self.assertEqual(len(self.started), 1)  # not yet
        self.clock.now += 40
        self.cable.tick([port()])
        self.assertEqual(len(self.started), 2)

    def test_wifi_is_ignored(self):
        wifi = {"device": "wlp1s0", "type": "wifi", "state": "disconnected", "connection": ""}
        self.clock.now += 100
        self.cable.tick([wifi])
        self.assertEqual(self.started, [])


class ProfileTests(unittest.TestCase):
    def test_link_local_and_never_an_address_server(self):
        calls = []

        def run(args, timeout=15.0):
            calls.append(args)
            if args[:2] == ["-g", "connection.interface-name"]:
                return 0, "", ""
            return 0, "", ""

        with mock.patch.object(direct.network, "_run", run):
            self.assertEqual(direct.go_direct("enp0s20u1", "home"), "")
        add = next(a for a in calls if a[:2] == ["connection", "add"])
        self.assertIn("link-local", add)
        self.assertNotIn("shared", add)
        self.assertEqual(add[add.index("connection.zone") + 1], "home")
        self.assertEqual(add[add.index("connection.autoconnect") + 1], "no")


class NasAddressTests(unittest.TestCase):
    def test_direct_address_is_listed_and_open_with_the_cable(self):
        ip = ("2: enp0s20u1    inet 169.254.33.7/16 brd 169.254.255.255 scope link enp0s20u1\n"
              "1: lo    inet 127.0.0.1/8 scope host lo\n")
        done = mock.Mock(stdout=ip)
        devices = [{"device": "enp0s20u1", "type": "ethernet", "state": "connected", "connection": direct.DIRECT}]
        with mock.patch.object(nas.subprocess, "run", lambda *a, **k: done), \
                mock.patch.object(nas.network, "devices", lambda: devices):
            found = nas.addresses()
        self.assertEqual([(a["address"], a["kind"]) for a in found], [("169.254.33.7", "direct")])
        nas.mark_reach(found, {"nas_cable": True})
        self.assertTrue(found[0]["open"])


if __name__ == "__main__":
    unittest.main()
