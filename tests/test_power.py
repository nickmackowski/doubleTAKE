"""The power mode, the power button's setting, and sleeping on the charger (1.1.2c)."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_screens import StationBase  # noqa: E402

from doubletake import powerctl, settings, station as station_module  # noqa: E402


class PowerctlTests(unittest.TestCase):
    def test_the_power_mode_is_read_from_either_name_of_the_service(self):
        answers = iter([(1, ""), (0, "(<'balanced'>,)\n")])
        with mock.patch.object(powerctl, "_run", lambda args, timeout=10: next(answers)):
            self.assertEqual(powerctl.get_mode(), "balanced")
        with mock.patch.object(powerctl, "_run", lambda args, timeout=10: (1, "")):
            self.assertEqual(powerctl.get_mode(), "")  # no power modes here: the row is hidden

    def test_only_the_three_modes_are_set(self):
        sent = []
        with mock.patch.object(powerctl, "_run", lambda args, timeout=10: sent.append(args) or (0, "")):
            self.assertFalse(powerctl.set_mode("turbo"))
            self.assertTrue(powerctl.set_mode("power-saver"))
        self.assertEqual(sent[0][-1], "<'power-saver'>")

    def test_idle_time_from_the_desktop(self):
        with mock.patch.object(powerctl, "_run", lambda args, timeout=10: (0, "(uint64 125000,)\n")):
            self.assertEqual(powerctl.idle_seconds(), 125.0)
        with mock.patch.object(powerctl, "_run", lambda args, timeout=10: (1, "")):
            self.assertIsNone(powerctl.idle_seconds())

    def test_an_update_running_is_seen(self):
        root = tempfile.mkdtemp()
        for pid, comm in (("1", "systemd"), ("42", "gnome-shell")):
            os.makedirs(os.path.join(root, pid))
            Path(root, pid, "comm").write_text(comm + "\n")
        self.assertFalse(powerctl.updating(root))
        os.makedirs(os.path.join(root, "77"))
        Path(root, "77", "comm").write_text("dnf\n")
        self.assertTrue(powerctl.updating(root))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"library": tempfile.mkdtemp()}
        patch = mock.patch.object(settings.config, "save", lambda changes: None)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_new_choices(self):
        with mock.patch.object(powerctl, "gnome_button", lambda action: None):
            self.assertEqual(settings.apply(self.cfg, {"screen_dark_after": 30, "sleep_on_charger": 60,
                                                       "power_button": "off"}), "")
        self.assertEqual((self.cfg["screen_dark_after"], self.cfg["sleep_on_charger"], self.cfg["power_button"]),
                         (30, 60, "off"))
        self.assertTrue(settings.apply(self.cfg, {"sleep_on_charger": 45}))
        self.assertTrue(settings.apply(self.cfg, {"power_button": "hibernate"}))

    def test_the_power_mode_is_the_systems_own(self):
        with mock.patch.object(powerctl, "set_mode", lambda mode: True):
            self.assertEqual(settings.apply(self.cfg, {"power_mode": "performance"}), "")
        self.assertNotIn("power_mode", self.cfg)  # not kept here
        with mock.patch.object(powerctl, "set_mode", lambda mode: False):
            self.assertIn("could not", settings.apply(self.cfg, {"power_mode": "balanced"}))


class SleepTests(StationBase):
    def setUp(self):
        super().setUp()
        self.slept = []
        for name, value in (("suspend", lambda: self.slept.append(True) or True), ("set_power", lambda on: True)):
            patch = mock.patch.object(station_module.screen, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(station_module.powerctl, "idle_seconds", lambda: None)
        patch.start()
        self.addCleanup(patch.stop)

    def left_alone(self, minutes, on_power):
        self.station.tablet = {"battery": 80, "on_power": on_power}
        self.station._touch_at = time.time() - minutes * 60

    def test_on_the_charger_it_sleeps_after_its_own_wait(self):
        self.left_alone(59, on_power=True)
        self.station._idle(busy=False)
        self.assertEqual(self.slept, [])
        self.left_alone(61, on_power=True)
        self.station._idle(busy=False)
        self.assertEqual(self.slept, [True])
        self.station.cfg["sleep_on_charger"] = 0
        self.left_alone(600, on_power=True)
        self.station._idle(busy=False)
        self.assertEqual(self.slept, [True])  # Never: for a desk or a dock

    def test_never_while_busy(self):
        self.left_alone(120, on_power=True)
        self.station._idle(busy=True)
        self.assertEqual(self.slept, [])

    def test_a_touch_on_the_desktop_counts(self):
        self.left_alone(120, on_power=False)
        with mock.patch.object(station_module.powerctl, "idle_seconds", lambda: 30.0):
            self.station._idle(busy=False)
        self.assertEqual(self.slept, [])

    def test_a_computer_copying_from_the_nas_keeps_it_awake(self):
        with mock.patch.object(station_module.nas, "in_use", lambda: True):
            self.station._nas_used_at = 0
            # the station's tick marks the time; a minute later it still counts as busy
            self.station._nas_used_at = time.time() - 60
        self.assertLess(time.time() - self.station._nas_used_at, station_module.NAS_GRACE)


class UpdateLookTests(StationBase):
    """The quiet look for updates: on a trusted network, never while copying, never on the hotspot."""

    def setUp(self):
        super().setUp()
        self.looked = []
        patch = mock.patch.object(station_module.updates, "look",
                                  lambda cfg: self.looked.append(True) or {"available": True, "version": "9c",
                                                                           "system": 0, "security": 0, "later": False})
        patch.start()
        self.addCleanup(patch.stop)

    def tick(self, kinds, copying=False, opened=True):
        found = [{"kind": k, "network": "home", "open": opened} for k in kinds]
        with mock.patch.object(station_module.nas, "addresses", lambda: found), \
                mock.patch.object(station_module.nas, "mark_reach", lambda f, cfg: f):
            self.station._update_at = 0
            self.station._update_tick(copying)
            if self.station._update_thread:
                self.station._update_thread.join(5)

    def test_on_a_trusted_network_it_looks(self):
        self.tick(["wifi"])
        self.assertEqual(self.looked, [True])
        self.assertEqual(self.station.state()["update"]["version"], "9c")

    def test_not_while_copying_on_the_hotspot_or_an_untrusted_network(self):
        self.tick(["wifi"], copying=True)
        self.tick(["hotspot"])
        self.tick(["wifi"], opened=False)
        self.assertEqual(self.looked, [])


if __name__ == "__main__":
    unittest.main()
