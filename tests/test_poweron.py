"""Tests for Start at power-on: automatic login, opening at login, and no lock or blanking."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from doubletake import config, poweron, settings  # noqa: E402


class FakeDesktop:
    """Stands in for gsettings, holding the desktop's values."""

    def __init__(self):
        self.values = {
            ("org.gnome.desktop.screensaver", "lock-enabled"): "true",
            ("org.gnome.desktop.session", "idle-delay"): "uint32 300",
            ("org.gnome.settings-daemon.plugins.power", "sleep-inactive-ac-type"): "'suspend'",
            ("org.gnome.desktop.notifications", "show-in-lock-screen"): "true",
        }

    def __call__(self, *args):
        verb, schema, key = args[0], args[1], args[2]
        if (schema, key) not in self.values:
            return 1, ""
        if verb == "range":
            return 0, "type"
        if verb == "get":
            return 0, self.values[(schema, key)]
        if verb == "set":
            self.values[(schema, key)] = args[3]
            return 0, ""
        return 1, ""


class PowerOnTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="doubletake-home-")
        self.gdm = Path(self.home) / "custom.conf"
        self.gdm.write_text("[daemon]\n\n[security]\n")
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.home})
        env.start()
        self.addCleanup(env.stop)
        self.desktop = FakeDesktop()
        self.calls = []

        def helper(*args, stdin=None, timeout=300):
            self.calls.append(args)
            if args == ("autologin", "on"):
                self.gdm.write_text("[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=pilot\n\n[security]\n")
            if args == ("autologin", "off"):
                self.gdm.write_text("[daemon]\n\n[security]\n")
            return {"ok": True}

        for target, name, value in ((poweron, "GDM_CONF", self.gdm), (poweron, "_gsettings", self.desktop),
                                    (poweron.nas, "helper", helper),
                                    (settings, "AUTOSTART", Path(self.home) / "autostart" / "doubletake.desktop")):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cfg = config.load()

    def test_on_and_off(self):
        self.assertFalse(poweron.is_on("pilot"))
        self.assertEqual(poweron.switch(self.cfg, True, "pilot"), "")
        self.assertTrue(poweron.is_on("pilot"))
        self.assertEqual(poweron.autologin_user(), "pilot")
        self.assertEqual(self.desktop.values[("org.gnome.desktop.screensaver", "lock-enabled")], "false")
        self.assertEqual(self.desktop.values[("org.gnome.desktop.session", "idle-delay")], "uint32 0")
        # switching on twice keeps the desktop's own values, not ours
        poweron.switch(self.cfg, True, "pilot")
        self.assertEqual(config.read_user()["desktop_saved"]["org.gnome.desktop.session idle-delay"], "uint32 300")
        self.assertEqual(poweron.switch(self.cfg, False, "pilot"), "")
        self.assertFalse(poweron.is_on("pilot"))
        self.assertEqual(self.desktop.values[("org.gnome.desktop.screensaver", "lock-enabled")], "true")
        self.assertEqual(self.desktop.values[("org.gnome.settings-daemon.plugins.power", "sleep-inactive-ac-type")],
                         "'suspend'")
        self.assertEqual(config.read_user()["desktop_saved"], {})

    def test_the_fz_m1s_ctrl_alt_del_does_not_log_out(self):
        # Its A button sends Ctrl+Alt+Del (night mode in DoubleTake): no Log Out box for it.
        logout = ("org.gnome.settings-daemon.plugins.media-keys", "logout")
        self.desktop.values[logout] = "['<Control><Alt>Delete']"
        with mock.patch.object(poweron, "_product", lambda: "FZM1-3"):
            poweron.switch(self.cfg, True, "pilot")
            self.assertEqual(self.desktop.values[logout], "@as []")
            poweron.switch(self.cfg, False, "pilot")
        self.assertEqual(self.desktop.values[logout], "['<Control><Alt>Delete']")
        with mock.patch.object(poweron, "_product", lambda: "Latitude 7212 Rugged Extreme Tablet"):
            poweron.switch(self.cfg, True, "pilot")
        self.assertEqual(self.desktop.values[logout], "['<Control><Alt>Delete']")  # the Dells: as it was

    def test_other_users_autologin_is_not_ours(self):
        self.gdm.write_text("[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=someone\n")
        self.assertEqual(poweron.autologin_user(), "someone")
        self.assertFalse(poweron.is_on("pilot"))

    def test_disabled_autologin(self):
        self.gdm.write_text("[daemon]\nAutomaticLoginEnable=False\nAutomaticLogin=pilot\n")
        self.assertEqual(poweron.autologin_user(), "")


if __name__ == "__main__":
    unittest.main()
