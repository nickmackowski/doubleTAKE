"""The tablet's own keys: P1 dark backup, P2 night red, P3 the screen."""

from __future__ import annotations

import os
import struct
import sys
import time
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
    def input_device(self, name, codes, title="Some device"):
        words = [0] * 12  # KEY_MAX 0x2ff, as the kernel gives it
        for code in codes:
            words[code // 64] |= 1 << (code % 64)
        folder = os.path.join(self.root, "sys/class/input", name, "device/capabilities")
        os.makedirs(folder)
        with open(os.path.join(folder, "key"), "w") as fh:
            fh.write(" ".join(f"{w:x}" for w in reversed(words)) + "\n")
        with open(os.path.join(folder, "..", "name"), "w") as fh:
            fh.write(title + "\n")

    def test_the_7212_windows_button_comes_through_the_built_in_keyboard_and_nothing_else(self):
        import io

        self.input_device("event10", [114, 115, 116, 125, 561], "Intel HID 5 button array")
        self.input_device("event7", [30, 31, 125], "AT Translated Set 2 keyboard")
        self.input_device("event20", [30, 31, 125], "USB keyboard")  # another keyboard: never
        self.assertEqual(helper.keyboard_devices(self.sys), ["/dev/input/event7"])
        os.makedirs(os.path.join(self.root, "dev/input"))
        open(os.path.join(self.root, "dev/input/event10"), "wb").close()
        with open(os.path.join(self.root, "dev/input/event7"), "wb") as fh:  # typing, then Windows
            fh.write(event(30, 1) + event(30, 0) + event(125, 1) + event(31, 1) + event(125, 0))
        calls = []

        def select(files, *a):
            calls.append(1)
            if len(calls) > 1:
                raise BrokenPipeError
            return [files[-1]], [], []

        out = io.StringIO()
        with mock.patch("select.select", select):
            helper.watch_keys(self.sys, out)
        self.assertEqual(out.getvalue().splitlines()[1:], ['{"key": "windows"}', '{"key": "windows_up"}'])

    def test_only_the_p_keys_device_is_used_never_a_keyboard(self):
        self.input_device("event15", [148, 149, 202])        # the firmware's hotkeys
        self.input_device("event3", [30, 31, 148, 202])      # a keyboard with launch keys too
        self.input_device("event4", [16, 17, 148])           # letters, but no A: still a keyboard
        self.input_device("event7", [113, 114, 115])         # volume keys only: read too (the 7212)
        self.assertEqual(helper.key_devices(self.sys), ["/dev/input/event15", "/dev/input/event7"])

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

    def test_the_7212_windows_and_rotate_buttons(self):
        self.input_device("event13", [114, 115, 125, 561])  # Intel HID 5 button array
        self.assertEqual(helper.key_devices(self.sys), ["/dev/input/event13"])
        data = event(125, 1) + event(125, 0) + event(561, 1) + event(561, 0) + event(115, 1)
        self.assertEqual(helper.key_presses(data), ["windows", "windows_up", "rotate", "volume_up"])

    def fz_m1(self):
        os.makedirs(os.path.join(self.root, "sys/class/dmi/id"), exist_ok=True)
        with open(os.path.join(self.root, "sys/class/dmi/id/product_name"), "w") as fh:
            fh.write("FZM1-3\n")

    @staticmethod
    def at(t, code, value):
        return EVENT.pack(int(t), int(round((t - int(t)) * 1e6)), 1, code, value)

    def test_the_fz_m1_buttons_as_measured(self):
        # Nick's FZ-M1 (2026-11-23). Windows: the Windows key, 2 ms. Rotate: the Windows key held
        # round scancode 0x65 (given the rotate key, 561), about 0.2 s.
        at = self.at
        windows = at(1.0, 125, 1) + at(1.002, 125, 0)
        rotate = at(5.0, 125, 1) + at(5.001, 561, 1) + at(5.2, 561, 0) + at(5.201, 125, 0)
        self.assertEqual(helper.key_presses(windows + rotate, wrapped=True), ["windows", "windows_up", "rotate"])
        # Any other tablet: as before.
        self.assertEqual(helper.key_presses(windows, wrapped=False), ["windows", "windows_up"])
        held = at(9.0, 125, 1)  # its release not here yet: waited on (watch_keys)
        self.assertTrue(helper._open_windows_press(held))
        self.assertFalse(helper._open_windows_press(windows))

    def test_the_fz_m1_is_known_by_its_product_name(self):
        self.fz_m1()
        self.assertEqual(helper.model_keys(self.sys)["scancodes"], ((0x65, 561),))
        with open(os.path.join(self.root, "sys/class/dmi/id/product_name"), "w") as fh:
            fh.write("Latitude 7212 Rugged Extreme Tablet\n")
        self.assertEqual(helper.model_keys(self.sys), {})

    def test_the_fz_m1_rotate_button_through_the_built_in_keyboard(self):
        import io

        self.fz_m1()
        self.input_device("event2", [30, 31, 125], "AT Translated Set 2 keyboard")
        os.makedirs(os.path.join(self.root, "dev/input"))
        with open(os.path.join(self.root, "dev/input/event2"), "wb") as fh:  # rotate, then Windows
            fh.write(self.at(5.0, 125, 1) + self.at(5.001, 561, 1) + self.at(5.2, 561, 0) + self.at(5.201, 125, 0)
                     + self.at(7.0, 125, 1) + self.at(7.002, 125, 0))
        given, calls = [], []

        def select(files, *a):
            calls.append(1)
            if len(calls) > 1:
                raise BrokenPipeError
            return [files[0]], [], []

        out = io.StringIO()
        with mock.patch("fcntl.ioctl", lambda fh, request, arg: given.append((request, struct.unpack("II", arg)))), \
                mock.patch("select.select", select):
            helper.watch_keys(self.sys, out)
        self.assertEqual(given, [(0x40084504, (0x65, 561))])
        lines = out.getvalue().splitlines()
        self.assertIn('"rotate"', lines[0])
        self.assertEqual(lines[1:], ['{"key": "rotate"}', '{"key": "windows"}', '{"key": "windows_up"}'])

    def test_a_button_from_the_kernels_log(self):
        # The FZ-M1's hotkey 0x005e is its Windows button (also the Windows key): not read.
        self.assertNotIn("kmsg", helper.MODELS["FZM1-3"])
        mapping = {"005e": "p2"}
        line = b"3,1234,232092689,-;Unknown hotkey event: 0x005e\n"
        self.assertEqual(helper.kmsg_presses(line, mapping), ["p2"])
        self.assertEqual(helper.kmsg_presses(b"3,1235,232242786,-;Unknown hotkey event: 0x005f\n", mapping), [])
        self.assertEqual(helper.kmsg_presses(b"6,1236,1,-;usb 1-1: new device\n", mapping), [])

    def test_the_fz_m1_a_button_as_ctrl_alt_del(self):
        # Measured on Nick's FZ-M1 (A set to Ctrl+Alt+Del in its Setup Utility).
        at = self.at
        a = (at(12.613, 56, 1) + at(12.614, 29, 1) + at(12.616, 111, 1) + at(12.618, 111, 0)
             + at(12.619, 29, 0) + at(12.620, 56, 0))
        self.assertEqual(helper.key_presses(a, ctrl_alt_del="p2"), ["p2"])
        self.assertEqual(helper.key_presses(a), [])  # not on other tablets
        self.assertEqual(helper.key_presses(at(1, 111, 1) + at(1.1, 111, 0), ctrl_alt_del="p2"), [])  # Delete alone
        held = set()  # the Ctrl and Alt in one read, the Delete in the next
        self.assertEqual(helper.key_presses(a[:2 * helper.EVENT.size], ctrl_alt_del="p2", held=held), [])
        self.assertEqual(helper.key_presses(a[2 * helper.EVENT.size:], ctrl_alt_del="p2", held=held), ["p2"])
        self.assertEqual(held, set())

    def test_the_fz_m1_a_button_through_the_built_in_keyboard(self):
        import io

        self.fz_m1()
        self.input_device("event2", [30, 31, 125], "AT Translated Set 2 keyboard")
        os.makedirs(os.path.join(self.root, "dev/input"))
        with open(os.path.join(self.root, "dev/input/event2"), "wb") as fh:
            fh.write(self.at(12.613, 56, 1) + self.at(12.614, 29, 1) + self.at(12.616, 111, 1)
                     + self.at(12.618, 111, 0) + self.at(12.619, 29, 0) + self.at(12.620, 56, 0))
        calls = []

        def select(files, *a):
            calls.append(1)
            if len(calls) > 1:
                raise BrokenPipeError
            return [files[0]], [], []

        out = io.StringIO()
        with mock.patch("fcntl.ioctl", lambda *a: None), mock.patch("select.select", select):
            helper.watch_keys(self.sys, out)
        lines = out.getvalue().splitlines()
        self.assertIn('"p2"', lines[0])
        self.assertEqual(lines[1:], ['{"key": "p2"}'])

    def test_no_model_keys_on_other_tablets(self):
        os.makedirs(os.path.join(self.root, "sys/class/dmi/id"))
        with open(os.path.join(self.root, "sys/class/dmi/id/product_name"), "w") as fh:
            fh.write("Latitude 7212 Rugged Extreme Tablet\n")
        os.makedirs(os.path.join(self.root, "dev/input"))
        open(os.path.join(self.root, "dev/input/event2"), "wb").close()
        given = []
        with mock.patch("fcntl.ioctl", lambda *a: given.append(a)):
            self.assertEqual(helper.give_keycodes(self.sys, ["/dev/input/event2"]), set())
        self.assertEqual(given, [])

    def test_the_power_button_is_read_and_said(self):
        self.input_device("event1", [116])  # the power button's own small device
        self.assertEqual(helper.key_devices(self.sys), ["/dev/input/event1"])
        self.assertEqual(helper.key_presses(event(116, 1) + event(116, 0)), ["power"])

    def test_the_keys_read_are_said_first(self):
        import io

        self.input_device("event1", [116])
        self.input_device("event15", [148, 149])
        os.makedirs(os.path.join(self.root, "dev/input"))
        for name in ("event1", "event15"):
            open(os.path.join(self.root, "dev/input", name), "wb").close()
        out = io.StringIO()
        with mock.patch("select.select", side_effect=BrokenPipeError):
            helper.watch_keys(self.sys, out)
        self.assertEqual(out.getvalue().splitlines()[0], '{"have": ["p1", "p2", "power"]}')

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

    def acted(self):
        done = []
        patch = mock.patch.object(station_module.powerctl, "act", lambda action: done.append(action) or True)
        patch.start()
        self.addCleanup(patch.stop)
        return done

    def test_windows_short_press_brings_the_home_screen(self):
        self.station.screen_dark = True
        self.station.status_open = True
        self.station.on_key("windows")
        self.station.on_key("windows_up")
        self.assertEqual(self.station.state()["home_id"], 1)
        self.assertFalse(self.station.status_open)
        self.assertEqual(self.power, [True])  # lit again

    def test_windows_held_turns_the_screen_off(self):
        self.station.WINDOWS_HOLD = 0.05
        self.station.on_key("windows")
        time.sleep(0.2)
        self.station.on_key("windows_up")
        self.assertEqual(self.power, [False])
        self.assertTrue(self.station.screen_dark)
        self.assertEqual(self.station.state()["home_id"], 0)  # not Home as well

    def test_volume_keys_read_here_change_the_volume_and_the_screens_leave_them(self):
        from doubletake import media

        self.station.keys = mock.Mock(have={"volume_up", "volume_down", "p1"})
        with mock.patch.object(station_module.kiosk, "in_kiosk", lambda: True), \
                mock.patch.object(media, "press", lambda key: {"kind": "volume", "level": 55, "muted": False}):
            self.station.on_key("volume_up")
        state = self.station.state()
        self.assertEqual((state["level_shown"]["kind"], state["level_shown"]["level"]), ("volume", 55))
        self.assertTrue(state["keys_volume"])

    def test_rotate_steps_the_brightness_up_to_full_and_back_down(self):
        from doubletake import media

        level = {"now": 60}
        with mock.patch.object(station_module.kiosk, "in_kiosk", lambda: True), \
                mock.patch.object(media.screen, "brightness", lambda: level["now"]), \
                mock.patch.object(media.screen, "set_brightness", lambda v: level.update(now=v) or True):
            seen = []
            for _ in range(7):
                self.station.on_key("rotate")
                shown = self.station.state()["level_shown"]
                seen.append((shown["level"], shown["up"]))
        self.assertEqual(seen, [(75, True), (100, False), (75, False), (50, False), (25, True), (50, True), (75, True)])

    def test_a_rounded_or_late_brightness_does_not_swallow_a_press(self):
        from doubletake import media

        level = {"now": 50}
        with mock.patch.object(station_module.kiosk, "in_kiosk", lambda: True), \
                mock.patch.object(media.screen, "brightness", lambda: level["now"] - 1), \
                mock.patch.object(media.screen, "set_brightness", lambda v: level.update(now=v) or True):
            seen = []
            for _ in range(3):
                self.station.on_key("rotate")
                seen.append(self.station.state()["level_shown"]["level"])
        self.assertEqual(seen, [50, 75, 100])  # 49 read back as 74, 74 as the 75 it was set to

    def test_the_bounce_without_the_power_service(self):
        # The FZ-M1: the power service does not set the brightness, the login service does.
        # Nick's six presses went 100, 75, 50, 75, 50, 75: now 100, 75, 50, 25, 50, 75.
        import tempfile
        from doubletake import media, screen

        root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, root, True)
        (root / "intel_backlight").mkdir()
        (root / "intel_backlight" / "max_brightness").write_text("1000\n")
        (root / "intel_backlight" / "brightness").write_text("750\n")

        def busctl(cmd, *a, **k):
            (root / "intel_backlight" / "brightness").write_text(cmd[-1] + "\n")
            return 0, ""

        shown, up, last = [], True, None
        with mock.patch.object(screen, "brightness", lambda: None), \
                mock.patch.object(screen, "set_brightness", lambda level: False), \
                mock.patch.object(media, "_run", busctl), \
                mock.patch.object(media.shutil, "which", lambda name: "/usr/bin/busctl"):
            for _ in range(6):
                got, up = media.bounce_brightness(up, root=root, last=last)
                shown.append(got["level"])
                last = got["level"]
        self.assertEqual(shown, [100, 75, 50, 25, 50, 75])

    def test_the_bounce_never_goes_below_a_quarter(self):
        from doubletake import media

        self.assertEqual(media.next_bounce(5, False), (25, True))
        self.assertEqual(media.next_bounce(100, True), (75, False))

    def test_the_power_button_does_as_settings_say_with_no_question(self):
        done = self.acted()
        self.station.on_key("power")
        self.station.on_key("power")  # the same press, from a second device: once only
        self.station._power_key_at = 0
        self.station.cfg["power_button"] = "off"
        self.station.on_key("power")
        self.assertEqual(done, ["sleep", "off"])

    def test_during_a_backup_the_power_button_waits_and_can_be_cancelled(self):
        done = self.acted()
        self.station.backup = {"running": True}
        self.station.on_key("power")
        self.assertEqual((done, self.station.power_pending), ([], "sleep"))
        self.assertEqual(self.power[-1], False)  # the screen goes dark meanwhile
        self.station._power_tick(copying=True)
        self.assertEqual(done, [])
        self.station._power_tick(copying=False)  # done, and both copies verified
        self.assertEqual((done, self.station.power_pending), (["sleep"], ""))
        # Pressed again while it waits: kept on.
        self.station._power_key_at = 0
        self.station.on_key("power")
        self.station._power_key_at = 0
        self.station.on_key("power")
        self.assertEqual(self.station.power_pending, "")
        self.station._power_tick(copying=False)
        self.assertEqual(done, ["sleep"])

    def test_the_button_is_held_from_the_system_only_where_its_presses_come_through(self):
        held = []
        self.station.button = mock.Mock(hold=lambda: held.append(True))
        self.station.keys = mock.Mock(have={"p1", "p2"})
        self.station.on_key("have")
        self.assertEqual(held, [])
        self.station.keys = mock.Mock(have={"p1", "power"})
        self.station.on_key("have")
        self.assertEqual(held, [True])

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


class BootScreenTests(unittest.TestCase):
    def test_only_a_round_firmware_logo_sets_the_rings_size(self):
        # The Dells' round logo: the ring takes its size and place. The FZ-M1's wide word
        # (about 680 wide): followed, the ring filled two thirds of the screen; now the ring is
        # half the screen's height (B, Nick's choice), all of it centred on the screen.
        script = (Path(__file__).resolve().parent.parent / "system/plymouth/doubletake.script").read_text()
        self.assertIn("if (round_logo) {\n    scale = side / 366;\n} else if (wide_logo) {\n"
                      "    scale = Window.GetHeight() * 0.5 / 366;\n} else if (514 > Window.GetHeight() * 0.45)", script)
        self.assertIn("} else if (wide_logo) {\n    logo.x = Window.GetX() + Window.GetWidth() / 2 - logo.w / 2;\n"
                      "    logo.y = Window.GetY() + Window.GetHeight() / 2 - logo.h / 2;", script)
        self.assertIn("if (handoff) {\n    if (round_logo == 0) {\n        wide_logo = 1;", script)
