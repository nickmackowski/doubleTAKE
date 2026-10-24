"""Tests for maintenance mode: one visit to the plain desktop, from DoubleTake only."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from doubletake import cli, maintenance  # noqa: E402


class FakeDesktop:
    """gsettings, as a dictionary."""

    def __init__(self):
        self.values = {
            ("org.gnome.desktop.background", "picture-uri"): "'file:///usr/share/backgrounds/rhel.jpg'",
            ("org.gnome.desktop.background", "picture-options"): "'zoom'",
            ("org.gnome.desktop.background", "primary-color"): "'#000000'",
            ("org.gnome.shell", "favorite-apps"): "['firefox.desktop', 'org.gnome.Nautilus.desktop']",
        }

    def __call__(self, *args):
        verb, schema, key = args[0], args[1], args[2]
        if (schema, key) not in self.values:
            return 1, ""
        if verb == "set":
            self.values[(schema, key)] = args[3]
        return 0, self.values[(schema, key)]


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doubletake-maint-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.desktop = FakeDesktop()
        self.helper = []
        patches = [
            mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.tmp / "config"), "DESKTOP_SESSION": "gnome"}),
            mock.patch.object(maintenance, "AUTOSTART", self.tmp / "autostart" / "doubletake-maintenance.desktop"),
            mock.patch.object(maintenance, "RESTART_ENTRY", self.tmp / "apps" / maintenance.RESTART_APP),
            mock.patch.object(maintenance, "WALLPAPER", self.tmp / "wallpaper.png"),
            mock.patch.object(maintenance, "_gsettings", self.desktop),
            mock.patch.object(maintenance, "_notify", lambda title, text: self.notes.append(text)),
            mock.patch.object(maintenance.nas, "helper", lambda *a, **k: self.helper.append(a) or {"ok": True}),
        ]
        os.environ.pop("DOUBLETAKE_KIOSK", None)
        self.notes = []
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        real = maintenance.make_wallpaper
        maintenance.make_wallpaper = lambda host="": real(maintenance.WALLPAPER, (960, 540), host)
        self.addCleanup(setattr, maintenance, "make_wallpaper", real)

    def test_one_visit(self):
        self.assertEqual(maintenance.start(), "")
        self.assertEqual(self.helper[-1], ("session", "desktop"))
        self.assertTrue(maintenance.active())
        self.assertIn("maintenance", maintenance.AUTOSTART.read_text())

        # The desktop login: the next login is DoubleTake again, and the desktop says so.
        self.assertEqual(maintenance.at_desktop_login(), 0)
        self.assertEqual(self.helper[-1], ("session", "kiosk"))
        uri = self.desktop.values[("org.gnome.desktop.background", "picture-uri")]
        self.assertIn("wallpaper.png", uri)
        self.assertTrue(maintenance.WALLPAPER.is_file())
        self.assertIn(maintenance.RESTART_APP, self.desktop.values[("org.gnome.shell", "favorite-apps")])
        self.assertIn("Restart the tablet", self.notes[-1])
        maintenance.at_desktop_login()  # a second run in the same visit changes nothing
        self.assertEqual([h for h in self.helper if h == ("session", "kiosk")], [("session", "kiosk")])

        # Back in DoubleTake only: the desktop as it was.
        maintenance.finish()
        self.assertEqual(self.desktop.values[("org.gnome.desktop.background", "picture-uri")],
                         "'file:///usr/share/backgrounds/rhel.jpg'")
        self.assertEqual(self.desktop.values[("org.gnome.shell", "favorite-apps")],
                         "['firefox.desktop', 'org.gnome.Nautilus.desktop']")
        self.assertFalse(maintenance.active())
        self.assertFalse(maintenance.AUTOSTART.exists())
        self.assertFalse(maintenance.RESTART_ENTRY.exists())

    def test_nothing_happens_in_the_kiosk_session(self):
        maintenance.start()
        with mock.patch.dict(os.environ, {"DOUBLETAKE_KIOSK": "1"}):
            maintenance.at_desktop_login()
        self.assertNotIn(("session", "kiosk"), self.helper)

    def test_a_refused_switch_starts_nothing(self):
        with mock.patch.object(maintenance.nas, "helper", lambda *a, **k: {"ok": False, "error": "no"}):
            self.assertEqual(maintenance.start(), "no")
        self.assertFalse(maintenance.active())
        self.assertFalse(maintenance.AUTOSTART.exists())

    def test_field_station_stays_closed_on_the_maintenance_desktop(self):
        maintenance.start()
        args = SimpleNamespace(autostart=True, source="camera", port=None, no_browser=True, window=False, limit=None)
        with mock.patch("doubletake.server.serve", lambda *a, **k: self.fail("must not open")):
            self.assertEqual(cli.cmd_gui(args, {}), cli.EXIT_OK)
        opened = []
        args.autostart = False  # the app list's DoubleTake icon still opens it
        with mock.patch("doubletake.server.serve", lambda *a, **k: opened.append(True) or 0):
            cli.cmd_gui(args, {})
        self.assertEqual(opened, [True])

    def test_the_kiosk_puts_the_desktop_back(self):
        maintenance.start()
        maintenance.at_desktop_login()
        args = SimpleNamespace(autostart=False, source="camera", port=None, no_browser=True, window=False, limit=None)
        with mock.patch.dict(os.environ, {"DOUBLETAKE_KIOSK": "1"}), \
                mock.patch("doubletake.server.serve", lambda *a, **k: 0):
            cli.cmd_gui(args, {})
        self.assertFalse(maintenance.active())

    def test_a_visit_chosen_in_the_boot_menu(self):
        with mock.patch.object(maintenance.nas, "system_state", lambda: {"boot_maintenance": True}):
            self.assertEqual(maintenance.at_desktop_login(), 0)
        self.assertEqual(self.helper[-1], ("boot-maintenance", "done"))
        self.assertIn("wallpaper.png", self.desktop.values[("org.gnome.desktop.background", "picture-uri")])
        self.assertTrue(maintenance.active())

    def test_an_ordinary_login_leaves_the_entry_ready(self):
        maintenance.write_autostart()
        self.assertEqual(maintenance.at_desktop_login(), 0)
        self.assertTrue(maintenance.AUTOSTART.exists())
        self.assertEqual(self.helper, [])

    def test_wallpaper(self):
        path = maintenance.make_wallpaper(host="rynzler")
        from PIL import Image

        with Image.open(path) as img:
            self.assertEqual(img.size, (960, 540))


if __name__ == "__main__":
    unittest.main()
