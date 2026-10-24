"""Tests for Bluetooth, against a stand-in bluetoothctl."""

from __future__ import annotations

import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from doubletake import bluetooth  # noqa: E402

KEYBOARD = "AA:BB:CC:11:22:33"
MOUSE = "AA:BB:CC:44:55:66"

FAKE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import sys, time
    args = sys.argv[1:]
    state = "%(state)s"
    if args == ["show"]:
        print("Controller 00:1A:7D:DA:71:13 (public)\\n\\tName: doubletake\\n\\tAlias: tablet\\n\\tPowered: yes\\n\\tDiscovering: no")
    elif args == ["devices"]:
        print("Device %(kb)s Folio Keyboard\\nDevice %(mouse)s MX Anywhere\\nDevice 11:22:33:44:55:66 11-22-33-44-55-66")
    elif args[:1] == ["info"]:
        paired = "yes" if args[1] == "%(mouse)s" else "no"
        icon = "input-mouse" if args[1] == "%(mouse)s" else "input-keyboard"
        alias = {"%(mouse)s": "MX Anywhere", "%(kb)s": "Folio Keyboard"}.get(args[1], args[1].replace(":", "-"))
        print("Device " + args[1] + "\\n\\tAlias: " + alias
              + "\\n\\tIcon: " + icon + "\\n\\tPaired: " + paired + "\\n\\tConnected: " + paired)
    elif args[:1] == ["power"]:
        if state == "blocked":
            print("Failed to set power on: org.bluez.Error.Blocked")
        else:
            print("Changing power on succeeded")
    elif args[:1] == ["remove"]:
        print("Device has been removed")
    elif not args:
        # interactive: pairing
        for line in sys.stdin:
            cmd = line.strip()
            if cmd.startswith("pair "):
                if state == "fail":
                    print("Failed to pair: org.bluez.Error.AuthenticationFailed", flush=True)
                else:
                    print("Attempting to pair with " + cmd[5:], flush=True)
                    print("[agent] Passkey: 482913", flush=True)
                    time.sleep(0.3)
                    print("Pairing successful", flush=True)
            elif cmd.startswith("connect "):
                print("Connection successful", flush=True)
            elif cmd == "quit":
                break
''')


class BluetoothTests(unittest.TestCase):
    def fake(self, state="ok"):
        folder = tempfile.mkdtemp(prefix="doubletake-bt-")
        path = Path(folder) / "bluetoothctl"
        path.write_text(FAKE % {"state": state, "kb": KEYBOARD, "mouse": MOUSE})
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        for name, value in (("tool", lambda: str(path)), ("service_running", lambda: True),
                            ("has_hardware", lambda root="": True)):
            patcher = mock.patch.object(bluetooth, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_status(self):
        self.fake()
        s = bluetooth.status()
        self.assertTrue(s["powered"])
        self.assertEqual(s["name"], "tablet")
        self.assertEqual([d["name"] for d in s["paired"]], ["MX Anywhere"])
        self.assertEqual(s["paired"][0]["kind"], "mouse")
        self.assertTrue(s["paired"][0]["connected"])
        self.assertEqual([d["name"] for d in s["nearby"]], ["Folio Keyboard"])  # the unnamed one is left out

    def test_pair_shows_the_passkey(self):
        self.fake()
        said = []
        self.assertEqual(bluetooth.pair(KEYBOARD, said.append, timeout=10), "")
        self.assertIn("Type 482913 on the device, then press Enter.", said)
        self.assertIn("Paired. Connecting...", said)

    def test_pair_failure(self):
        self.fake("fail")
        self.assertIn("Pairing failed", bluetooth.pair(KEYBOARD, lambda text: None, timeout=10))

    def test_blocked_adapter(self):
        self.fake("blocked")
        with mock.patch.object(bluetooth.shutil, "which", lambda name: None):
            self.assertIn("rfkill", bluetooth.power(True))

    def test_addresses_are_checked(self):
        self.assertEqual(bluetooth.remove("; rm -rf /"), "Not a Bluetooth address.")
        self.assertEqual(bluetooth.pair("nope", lambda t: None), "Not a Bluetooth address.")

    def test_no_controller(self):
        self.assertEqual(bluetooth.parse_show("No default controller available"), {})


if __name__ == "__main__":
    unittest.main()
