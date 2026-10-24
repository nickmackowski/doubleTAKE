"""Tests for the long-running engine service and the server behind the screens."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from fakes import make_card  # noqa: E402
from test_engine import Base  # noqa: E402

from doubletake import cards, power, station as station_module  # noqa: E402
from doubletake.server import make_handler  # noqa: E402
from doubletake.sources.folder import FolderSource  # noqa: E402
from doubletake.station import Station  # noqa: E402
from doubletake.util import STOPPED  # noqa: E402


class StationBase(Base):
    def setUp(self):
        super().setUp()
        os.environ.pop("DOUBLETAKE_CARD", None)
        self.sd = self.tmp / "SD_CARD"
        self.sd.mkdir()
        self.cfg["second_copy"] = str(self.sd)
        self.cfg["quick_names"] = ["KCLT-ramp"]
        make_card(self.card, count=6)
        patcher = mock.patch.object(station_module, "TICK_SECONDS", 0.05)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.station = Station(self.cfg, f"dir:{self.card}")
        self.addCleanup(self.station.shutdown)

    def wait(self, test, what, seconds=15):
        end = time.time() + seconds
        while time.time() < end:
            state = self.station.state()
            if test(state):
                return state
            time.sleep(0.03)
        self.fail(f"timed out waiting for {what}: {json.dumps(self.station.state())[:1500]}")

    def use_card(self):
        found = cards.detect(self.cfg, self.ledger)
        cards.tag(found.card, self.ledger)


class StationTests(StationBase):
    def test_camera_is_read_when_it_appears(self):
        self.station.start()
        state = self.wait(lambda s: s["camera"]["state"] == "ready" and s["format"].get("known"),
                          "the camera to be read")
        self.assertEqual((state["camera"]["photos"], state["camera"]["new"]), (6, 6))
        self.assertEqual(state["card"]["state"], "new")
        self.assertFalse(state["format"]["safe"])
        self.assertRegex(self.station.folder_preview("KCLT ramp"), r"^\d{4}_\d{4}_kclt_ramp_01$")

    def test_backup_then_second_copy_in_the_background_then_safe(self):
        self.use_card()
        self.station.start()
        self.wait(lambda s: s["camera"]["state"] == "ready", "the camera to be read")
        self.assertEqual(self.station.start_backup("KCLT ramp"), "")
        state = self.wait(lambda s: s["backup"] and not s["backup"]["running"], "the backup to finish")
        backup = state["backup"]
        self.assertEqual((backup["verified"], backup["failed"], backup["interrupted"]), (6, 0, False))
        self.assertTrue(backup["folder"].endswith("_kclt_ramp_01"))
        # Nobody asks for the second copy. It follows by itself.
        state = self.wait(lambda s: s["format"].get("safe"), "the second copy to finish")
        self.assertEqual(state["card"]["waiting"], 0)
        self.assertEqual(state["card"]["held"], 6)
        self.assertEqual(state["mirror"]["state"], "idle")
        self.assertEqual(state["shoots"][0]["mirrored"], 6)
        shoot = Path(backup["folder"]).relative_to(self.library)
        self.assertEqual(len(list((self.sd / "DoubleTake" / shoot).glob("*.NEF"))), 6)
        self.station.dismiss()
        self.assertIsNone(self.station.state()["backup"])

    def test_without_a_card_the_backup_still_runs_and_is_not_called_safe(self):
        self.cfg["second_copy"] = str(self.tmp / "nowhere")
        self.station.start()
        self.wait(lambda s: s["camera"]["state"] == "ready", "the camera to be read")
        self.station.start_backup("x")
        self.wait(lambda s: s["backup"] and not s["backup"]["running"], "the backup to finish")
        state = self.wait(lambda s: s["format"].get("on_tablet") == 6, "the count to settle")
        self.assertEqual(state["card"]["state"], "none")
        self.assertFalse(state["format"]["safe"])
        self.assertEqual(state["format"]["on_second"], 0)

    def test_stop_then_continue(self):
        self.use_card()
        real = FolderSource.read_chunk

        def slow(source, f, offset, size):
            time.sleep(0.08)
            return real(source, f, offset, size)

        with mock.patch.object(FolderSource, "read_chunk", slow):
            self.station.start()
            self.wait(lambda s: s["camera"]["state"] == "ready", "the camera to be read")
            self.station.start_backup("stop me")
            self.wait(lambda s: s["backup"] and s["backup"]["done_files"] >= 1, "the first file")
            self.station.stop_backup_now()
            state = self.wait(lambda s: s["backup"] and not s["backup"]["running"], "the backup to stop")
            self.assertTrue(state["backup"]["interrupted"])
            self.assertEqual(state["backup"]["reason"], STOPPED)
            self.assertLess(state["backup"]["verified"], 6)
        self.station.start_backup("stop me")
        self.wait(lambda s: s["backup"] and not s["backup"]["running"] and not s["backup"]["interrupted"],
                  "the backup to finish")
        self.wait(lambda s: s["format"].get("safe"), "the second copy to finish")

    def test_unattended_backup_when_the_camera_is_plugged_in(self):
        self.use_card()
        self.cfg["auto_backup"] = True
        powered = []
        with mock.patch.object(station_module, "AUTO_COUNTDOWN", 0.5), \
                mock.patch.object(station_module, "AUTO_DARK_AFTER", 0.0), \
                mock.patch.object(station_module.screen, "set_power", lambda on: powered.append(on) or True):
            self.station.start()
            state = self.wait(lambda s: s["auto"] and s["auto"]["state"] == "countdown", "the countdown")
            self.assertEqual(state["auto"]["name"], "")
            self.assertTrue(state["auto"]["folder"].endswith("_import_01"))
            state = self.wait(lambda s: s["backup"] and not s["backup"]["running"] and s["backup"]["verified"] == 6,
                              "the unattended backup to finish")
            self.assertTrue(state["backup"]["auto"])
            self.assertTrue(state["backup"]["folder"].endswith("_import_01"))
            self.assertIsNone(state["auto"])
            self.wait(lambda s: s["screen_dark"], "the screen to go dark")
            self.station.wake_screen()  # a touch
            self.assertEqual(powered[:2], [False, True])
        # The next one that day is _02.
        self.assertTrue(self.station.folder_preview("").endswith("_import_02"))

    def test_dark_screen_and_sleep_after_no_touch(self):
        powered, slept = [], []
        self.cfg.update(screen_dark_after=2, sleep_on_battery=15)
        with mock.patch.object(station_module.screen, "set_power", lambda on: powered.append(on) or True), \
                mock.patch.object(station_module.screen, "suspend", lambda: slept.append(True) or True):
            self.station.tablet = {"battery": 60, "on_power": True}
            self.station._touch_at = time.time() - 16 * 60
            self.station._idle(busy=False)
            self.assertEqual((powered, slept), ([False], []))  # on the charger: dark, but no sleep
            self.station.screen_dark = False
            self.station.tablet = {"battery": 60, "on_power": False}
            self.station._touch_at = time.time() - 16 * 60
            self.station._idle(busy=True)
            self.assertEqual(slept, [])  # never while copying
            self.station._idle(busy=False)
            self.assertEqual(slept, [True])
            self.station.wake_screen()
            self.station._idle(busy=False)
            self.assertEqual(slept, [True])  # a touch starts the count again

    def test_an_unattended_backup_can_be_cancelled(self):
        self.cfg["auto_backup"] = True
        with mock.patch.object(station_module, "AUTO_COUNTDOWN", 30.0):
            self.station.start()
            self.wait(lambda s: s["auto"] and s["auto"]["state"] == "countdown", "the countdown")
            self.station.cancel_auto()
            time.sleep(0.3)
            self.assertIsNone(self.station.state()["auto"])
            self.assertIsNone(self.station.state()["backup"])

    def test_off_by_default(self):
        self.station.start()
        self.wait(lambda s: s["camera"]["state"] == "ready", "the camera to be read")
        time.sleep(0.3)
        self.assertIsNone(self.station.state()["auto"])

    def test_a_low_battery_stops_an_unattended_backup_and_says_why(self):
        self.cfg["auto_backup"] = True
        with mock.patch.object(station_module, "AUTO_COUNTDOWN", 0.2), \
                mock.patch.object(Station, "power_problem", lambda st: "The camera battery is at 8%."):
            self.station.start()
            state = self.wait(lambda s: s["auto"] and s["auto"]["state"] == "blocked", "the backup to be refused")
            self.assertIn("8%", state["auto"]["message"])

    def test_an_idle_camera_battery_is_read_again(self):
        levels = iter([62, 61])
        with mock.patch.object(FolderSource, "battery_now", lambda src: next(levels, 61), create=True), \
                mock.patch.object(station_module, "CAMERA_BATTERY_EVERY", 0.0):
            self.station.start()
            self.wait(lambda s: s["camera"]["state"] == "ready", "the camera to be read")
            self.wait(lambda s: s["camera"]["battery"] in ("62%", "61%"), "the battery to be read again")

    def test_a_low_camera_battery_blocks_the_start(self):
        self.station.start()
        self.wait(lambda s: s["camera"]["state"] == "ready", "the camera to be read")
        with self.station.lock:
            self.station.camera["battery"] = "9%"
        self.assertIn("camera battery", self.station.start_backup("x"))
        self.assertIsNone(self.station.state()["backup"])
        self.assertTrue(self.station.state()["power_problem"])

    def test_a_low_tablet_battery_blocks_only_off_the_charger(self):
        self.station.start()
        self.wait(lambda s: s["camera"]["state"] == "ready", "the camera to be read")
        with mock.patch.object(Station, "power_problem", lambda st: power.problem("80%", 8, False)):
            self.assertIn("tablet", self.station.start_backup("x"))
        self.assertEqual(power.problem("80%", 8, True), "")

    def test_the_camera_battery_running_low_stops_after_the_photo_then_carries_on(self):
        self.use_card()
        levels = iter([50, 9])
        real = FolderSource.read_chunk

        def slow(source, f, offset, size):
            time.sleep(0.03)
            return real(source, f, offset, size)

        with mock.patch.object(FolderSource, "battery_now", lambda src: next(levels, 9), create=True), \
                mock.patch.object(FolderSource, "read_chunk", slow), \
                mock.patch.object(station_module._Progress, "POWER_EVERY", 0.0):
            self.station.start()
            self.wait(lambda s: s["camera"]["state"] == "ready", "the camera to be read")
            self.assertEqual(self.station.start_backup("battery"), "")
            state = self.wait(lambda s: s["backup"] and not s["backup"]["running"], "the backup to stop")
            self.assertTrue(state["backup"]["interrupted"])
            self.assertIn("battery", state["backup"]["reason"])
            self.assertEqual(state["backup"]["verified"], 2)  # the photo it was on was finished
            self.assertEqual(state["backup"]["failed"], 0)
        # A fresh battery: the camera is read again and the backup carries on.
        self.wait(lambda s: s["camera"]["state"] == "ready", "the camera to be read again")
        self.assertEqual(self.station.start_backup("battery"), "")
        state = self.wait(lambda s: s["backup"] and not s["backup"]["running"], "the backup to finish")
        self.assertFalse(state["backup"]["interrupted"])

    def test_pause_and_resume_the_second_copy(self):
        self.use_card()
        self.station.pause_mirror()
        self.station.start()
        self.wait(lambda s: s["camera"]["state"] == "ready", "the camera to be read")
        self.station.start_backup("paused")
        self.wait(lambda s: s["backup"] and not s["backup"]["running"], "the backup to finish")
        state = self.wait(lambda s: s["card"].get("waiting") == 6, "the photos to be waiting")
        time.sleep(0.3)
        self.assertEqual(self.station.state()["card"]["held"], 0)
        self.assertFalse(state["format"].get("safe"))
        self.station.resume_mirror()
        self.wait(lambda s: s["format"].get("safe"), "the second copy to finish")

    def test_a_new_card_is_used_only_after_the_answer(self):
        self.station.start()
        self.wait(lambda s: s["card"]["state"] == "new", "the new card to be noticed")
        self.assertEqual(list(self.sd.iterdir()), [])
        self.assertEqual(self.station.card_ignore(), "")
        self.wait(lambda s: s["card"]["state"] == "declined", "the card to be set aside")
        self.assertEqual(list(self.sd.iterdir()), [])
        self.assertEqual(self.station.card_use(), "")
        self.wait(lambda s: s["card"]["state"] == "ready", "the card to be in use")


class Copy2AlongsideTests(StationBase):
    def test_copy_2_waits_for_a_tenth_of_copy_1(self):
        st = self.station
        self.assertTrue(st._copy1_far_enough())  # no backup: free to run
        st.backup = {"running": True, "phase": "reading", "total_bytes": 0, "done_bytes": 0}
        self.assertFalse(st._copy1_far_enough())
        st.backup.update(phase="copying", total_bytes=1000, done_bytes=99)
        self.assertFalse(st._copy1_far_enough())
        st.backup.update(done_bytes=100)
        self.assertTrue(st._copy1_far_enough())
        st.backup.update(running=False)
        self.assertTrue(st._copy1_far_enough())


class ServerTests(StationBase):
    def setUp(self):
        super().setUp()
        self.use_card()
        self.quit = threading.Event()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.station, self.quit.set))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.station.start()

    def call(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as reply:
                return reply.status, reply.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_pages_and_state(self):
        status, body = self.call("/")
        self.assertEqual(status, 200)
        self.assertIn(b"DoubleTake", body)
        for path in ("/app.js", "/app.css", "/fonts/barlow-latin-600-normal.woff2"):
            self.assertEqual(self.call(path)[0], 200, path)
        status, body = self.call("/api/state")
        self.assertEqual(status, 200)
        self.assertIn("camera", json.loads(body))
        status, body = self.call("/api/folder?name=KCLT%20ramp")
        self.assertTrue(json.loads(body)["folder"].endswith("_kclt_ramp_01"))

    def test_only_this_tablets_own_screens_may_press_buttons(self):
        self.assertEqual(self.call("/api/backup/start", {"name": "x"})[0], 403)  # no header: another web page
        self.assertEqual(self.call("/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("/../config.py")[0], 404)
        self.assertEqual(self.call("/%2e%2e/%2e%2e/ledger.py")[0], 404)
        self.assertIsNone(self.station.state()["backup"])

    def test_the_ways_out_need_the_field_station_password(self):
        h = {"X-DoubleTake": "1", "Content-Type": "application/json"}
        checked = []

        def check(pw):
            checked.append(pw)
            return "" if pw == "ramp at dawn 27" else ("Enter the DoubleTake password." if not pw else "Wrong password.")

        with mock.patch.object(self.station, "check_password", check):
            for path, body in (("/api/quit", {}), ("/api/card/format", {}), ("/api/reset/settings", {}),
                               ("/api/reset/everything", {}), ("/api/leave", {"action": "desktop"}),
                               ("/api/settings", {"changes": {"kiosk": False}})):
                reply = json.loads(self.call(path, body, h)[1])
                self.assertTrue(reply.get("need_password"), path)
            self.assertFalse(self.quit.is_set())
            # Everyday buttons never ask.
            reply = json.loads(self.call("/api/settings", {"changes": {"theme": "night"}}, h)[1])
            self.assertTrue(reply["ok"])
            reply = json.loads(self.call("/api/leave", {"action": "explode"}, h)[1])
            self.assertNotIn("need_password", reply)
            # The right password lets Exit through.
            self.call("/api/quit", {"password": "ramp at dawn 27"}, h)
            self.assertTrue(self.quit.wait(2))

    def test_a_backup_started_from_the_screen(self):
        ours = {"X-DoubleTake": "1", "Content-Type": "application/json"}
        self.wait(lambda s: s["camera"]["state"] == "ready", "the camera to be read")
        status, body = self.call("/api/backup/start", {"name": "from screen"}, ours)
        self.assertEqual((status, json.loads(body)["ok"]), (200, True))
        state = self.wait(lambda s: s["backup"] and not s["backup"]["running"], "the backup to finish")
        self.assertEqual(state["backup"]["verified"], 6)
        self.wait(lambda s: s["format"].get("safe"), "the second copy to finish")
        self.call("/api/backup/dismiss", {}, ours)
        self.assertIsNone(self.station.state()["backup"])
        self.call("/api/quit", {}, ours)
        self.assertTrue(self.quit.wait(2))


if __name__ == "__main__":
    unittest.main()
