"""The tablet's own keys: P1 dark backup, P2 night red, P3 the screen."""

from __future__ import annotations

import os
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_helper import HelperBase, helper  # noqa: E402
from test_screens import StationBase  # noqa: E402

from doubletake import keys, settings, station as station_module  # noqa: E402

EVENT = struct.Struct("llHHi")


def event(code, value, kind=1):
    return EVENT.pack(0, 0, kind, code, value)


class HelperKeyTests(HelperBase):
    def input_device(self, name, codes):
        words = [0] * 4
        for code in codes:
            words[code // 64] |= 1 << (code % 64)
        folder = os.path.join(self.root, "sys/class/input", name, "device/capabilities")
        os.makedirs(folder)
        with open(os.path.join(folder, "key"), "w") as fh:
            fh.write(" ".join(f"{w:x}" for w in reversed(words)) + "\n")

    def test_only_the_p_keys_device_is_used_never_a_keyboard(self):
        self.input_device("event15", [148, 149, 202])        # the firmware's hotkeys
        self.input_device("event3", [30, 31, 148, 202])      # a keyboard with launch keys too
        self.input_device("event4", [16, 17, 148])           # letters, but no A: still a keyboard
        self.input_device("event7", [113, 114, 115])         # volume keys only
        self.assertEqual(helper.key_devices(self.sys), ["/dev/input/event15"])

    def test_the_brightness_keys_come_from_a_device_without_letters(self):
        self.input_device("event8", [224, 225])               # the Video Bus
        self.input_device("event3", [30, 31, 114, 115, 224])  # the built-in keyboard device: never
        self.assertEqual(helper.key_devices(self.sys), ["/dev/input/event8"])

    def test_brightness_held_down_goes_on_stepping(self):
        data = event(225, 1) + event(225, 2) + event(225, 2) + event(225, 0) + event(148, 2)
        self.assertEqual(helper.key_presses(data), ["brightness_up"] * 3)  # a held P key does not repeat

    def test_one_line_per_press_and_nothing_on_release(self):
        data = (event(148, 1) + event(148, 0) + event(0, 0, kind=0) + event(149, 1) + event(149, 0)
                + event(202, 1) + event(30, 1) + event(202, 2))
        self.assertEqual(helper.key_presses(data), ["p1", "p2", "p3"])

    def test_a_tablet_without_the_keys_says_so(self):
        os.makedirs(os.path.join(self.root, "sys/class/input"))
        self.assertFalse(self.job("keys")[1]["ok"])


class WatcherTests(unittest.TestCase):
    def test_each_press_is_handed_over(self):
        got = []
        script = "import json\nfor k in ('p1', 'x', 'p3'):\n    print(json.dumps({'key': k}), flush=True)\n"
        w = keys.KeyWatcher(got.append, command=[sys.executable, "-c", script], retry=0.05)
        w.start()
        self.assertTrue(keys.wait_until(lambda: len(got) >= 2))
        w.stop()
        self.assertEqual(got[:2], ["p1", "p3"])

    def test_no_keys_here_stops_listening(self):
        w = keys.KeyWatcher(lambda k: None, command=[sys.executable, "-c", "print('{\"ok\": false}')"], retry=0.05)
        w.start()
        w.thread.join(timeout=5)
        self.assertFalse(w.thread.is_alive())
        self.assertTrue(w.none_here)


class StationKeyTests(StationBase):
    def setUp(self):
        super().setUp()
        self.power = []
        patch = mock.patch.object(station_module.screen, "set_power", lambda on: self.power.append(on) or True)
        patch.start()
        self.addCleanup(patch.stop)
        apply = mock.patch.object(settings, "apply", self.fake_apply)
        apply.start()
        self.addCleanup(apply.stop)

    def fake_apply(self, cfg, changes):
        cfg.update(changes)
        return ""

    def sounds(self):
        heard = []
        real = self.station.beep

        def beep(kind):
            heard.append(kind)
            real(kind)

        self.station.beep = beep
        return heard

    def test_p1_backs_up_straight_away_with_beeps(self):
        heard = self.sounds()
        self.use_card()
        self.station.start()
        self.wait(lambda s: s["camera"]["state"] == "ready", "the camera to be read")
        self.assertEqual(self.station.one_touch_backup(), "")
        self.assertEqual(self.station.one_touch_backup(), "A backup is already running.")  # a second press: nothing
        state = self.wait(lambda s: s["backup"] and not s["backup"]["running"], "the backup to finish")
        self.assertEqual(state["backup"]["verified"], 6)
        self.assertEqual(heard, ["start", "done"])  # one beep, then two
        self.assertEqual(state["sound"]["kind"], "done")
        self.assertNotIn(False, self.power)  # the screen stays on

    def test_p1_with_no_camera_beeps_a_problem(self):
        heard = self.sounds()
        self.station.screen_dark = True
        self.assertEqual(self.station.one_touch_backup(), "No camera with new photos.")
        self.assertEqual(heard, ["problem"])
        self.assertEqual(self.power, [True])  # and the screen lights, to show why

    def test_p1_with_nothing_new_beeps_done(self):
        heard = self.sounds()
        self.station.camera.update(state="ready", new=0)
        self.assertEqual(self.station.one_touch_backup(), "Nothing new on the camera.")
        self.assertEqual(heard, ["done"])

    def test_beeps_can_be_switched_off(self):
        self.station.cfg["auto_chime"] = False
        self.station.beep("start")
        self.assertEqual(self.station.state()["sound"]["id"], 0)

    def test_p2_night_red_and_back_in_each_look(self):
        self.station.cfg.update(look="cockpit", cockpit_theme="cockpit-light")
        self.station.toggle_night_red()
        self.assertEqual(settings.screen_theme(self.station.cfg), ("cockpit", "cockpit-red"))
        self.station.toggle_night_red()
        self.assertEqual(settings.screen_theme(self.station.cfg), ("cockpit", "cockpit-light"))
        self.station.cfg.update(look="classic", theme="slate")
        self.station.toggle_night_red()
        self.assertEqual(settings.screen_theme(self.station.cfg), ("classic", "nightred"))
        self.station.toggle_night_red()
        self.assertEqual(settings.screen_theme(self.station.cfg), ("classic", "slate"))

    def test_brightness_keys_in_doubletake_only(self):
        from doubletake import media

        with mock.patch.object(station_module.kiosk, "in_kiosk", lambda: True), \
                mock.patch.object(media, "press", lambda key: {"kind": "brightness", "level": 60}):
            self.station.on_key("brightness_up")
        self.assertEqual(self.station.state()["level_shown"]["level"], 60)
        pressed = []
        with mock.patch.object(station_module.kiosk, "in_kiosk", lambda: False), \
                mock.patch.object(media, "press", lambda key: pressed.append(key) or {}):
            self.station.on_key("brightness_up")  # on the desktop, the desktop has them
        self.assertEqual(pressed, [])

    def test_p3_opens_and_closes_the_status(self):
        self.station.toggle_status()
        self.assertTrue(self.station.state()["status_open"])
        self.station.toggle_status()
        self.assertFalse(self.station.state()["status_open"])

    def test_the_status_counts_the_photos(self):
        self.use_card()
        self.station.start()
        self.wait(lambda s: s["camera"]["state"] == "ready", "the camera to be read")
        self.station.start_backup("")
        self.wait(lambda s: s["backup"] and not s["backup"]["running"], "the backup to finish")
        status = self.station.status()
        self.assertEqual(status["photos"], 6)
        self.assertEqual(status["shoots"], 1)
        self.assertGreater(status["copy1"]["bytes"], 0)
        self.assertIn("version", status)
        self.assertIn("power", status)

if __name__ == "__main__":
    unittest.main()
