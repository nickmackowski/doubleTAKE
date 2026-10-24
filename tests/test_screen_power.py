"""Tests for going dark: the backlight is turned down, and the panel is never powered off."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doubletake import screen  # noqa: E402


class DarkTests(unittest.TestCase):
    def setUp(self):
        screen._saved.clear()
        self.calls, self.level = [], 70

        def gdbus(*args):
            self.calls.append(args)
            if "org.freedesktop.DBus.Properties.Get" in args:
                return 0, f"(<{self.level}>,)\n"
            self.level = int(args[-1].split()[1].rstrip(">"))
            return 0, "()\n"

        p = mock.patch.object(screen, "_gdbus", gdbus)
        p.start()
        self.addCleanup(p.stop)

    def test_dark_and_lit_again(self):
        self.assertTrue(screen.set_power(False))
        self.assertEqual(self.level, 0)
        screen.set_power(False)  # dark twice: the first level is kept
        self.assertTrue(screen.set_power(True))
        self.assertEqual(self.level, 70)

    def test_never_powers_the_panel_off(self):
        screen.set_power(False)
        screen.set_power(True)
        self.assertFalse(any("PowerSaveMode" in " ".join(c) for c in self.calls))

    def test_without_the_power_service_the_cover_still_darkens(self):
        with mock.patch.object(screen, "_gdbus", lambda *a: (1, "")):
            self.assertTrue(screen.set_power(False))
            self.assertTrue(screen.set_power(True))


if __name__ == "__main__":
    unittest.main()
