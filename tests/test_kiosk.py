"""Tests for DoubleTake only (the kiosk session), and restarting or shutting down."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from doubletake import kiosk, server  # noqa: E402
from test_screens import StationBase  # noqa: E402


class SessionTests(unittest.TestCase):
    def test_knows_the_kiosk_session(self):
        self.assertTrue(kiosk.in_kiosk({"DESKTOP_SESSION": "gnome-kiosk-script-wayland"}))
        self.assertTrue(kiosk.in_kiosk({"DOUBLETAKE_KIOSK": "1"}))
        self.assertFalse(kiosk.in_kiosk({"DESKTOP_SESSION": "gnome", "XDG_CURRENT_DESKTOP": "GNOME"}))

    def test_a_desktop_autostart_in_the_kiosk_does_not_open_a_second_set_of_screens(self):
        self.assertTrue(kiosk.started_by_desktop_autostart({"XDG_SESSION_DESKTOP": "gnome-kiosk-script-wayland"}))
        self.assertFalse(kiosk.started_by_desktop_autostart(
            {"XDG_SESSION_DESKTOP": "gnome-kiosk-script-wayland", "DOUBLETAKE_KIOSK": "1"}))
        self.assertFalse(kiosk.started_by_desktop_autostart({"DESKTOP_SESSION": "gnome"}))


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doubletake-kiosk-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.script = self.tmp / "bin" / "gnome-kiosk-script"

    def test_the_script_starts_field_station(self):
        self.assertEqual(kiosk.write_script(self.script, "'/home/nick/.local/bin/doubletake'"), "")
        text = self.script.read_text()
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn("export DOUBLETAKE_KIOSK=1\n", text)
        self.assertIn("exec '/home/nick/.local/bin/doubletake' gui\n", text)
        self.assertTrue(os.access(self.script, os.X_OK))

    def test_someone_elses_script_is_kept_and_put_back(self):
        self.script.parent.mkdir(parents=True)
        self.script.write_text("#!/bin/sh\nexec gedit\n")
        kiosk.write_script(self.script, "doubletake")
        self.assertIn(kiosk.MARK, self.script.read_text())
        kiosk.write_script(self.script, "doubletake")  # again: the kept one is not overwritten by ours
        self.assertEqual((self.script.parent / "gnome-kiosk-script.before-doubletake").read_text(),
                         "#!/bin/sh\nexec gedit\n")
        kiosk.remove_script(self.script)
        self.assertEqual(self.script.read_text(), "#!/bin/sh\nexec gedit\n")

    def test_switching(self):
        asked = []
        with mock.patch.object(kiosk, "available", lambda: True), \
                mock.patch.object(kiosk.nas, "helper", lambda *a, **k: asked.append(a) or {"ok": True}):
            self.assertEqual(kiosk.switch(True, self.script), "")
            self.assertEqual(asked[-1], ("session", "kiosk"))
            self.assertIn(kiosk.MARK, self.script.read_text())
            self.assertEqual(kiosk.switch(False, self.script), "")
            self.assertEqual(asked[-1], ("session", "desktop"))
        self.assertFalse(self.script.exists())

    def test_a_refused_switch_leaves_no_script(self):
        with mock.patch.object(kiosk, "available", lambda: True), \
                mock.patch.object(kiosk.nas, "helper", lambda *a, **k: {"ok": False, "error": "no"}):
            self.assertEqual(kiosk.switch(True, self.script), "no")
        self.assertFalse(self.script.exists())

    def test_finds_the_session_by_its_rhel_9_name(self):
        wayland, xorg = self.tmp / "wayland-sessions", self.tmp / "xsessions"
        wayland.mkdir()
        xorg.mkdir()
        (wayland / "gnome.desktop").write_text("")
        self.assertFalse(kiosk.available((wayland, xorg)))
        (wayland / "gnome-kiosk-script.desktop").write_text("")
        self.assertTrue(kiosk.available((wayland, xorg)))

    def test_not_installed(self):
        with mock.patch.object(kiosk, "available", lambda: False):
            self.assertIn("doubletake setup", kiosk.switch(True))

    def test_power(self):
        ran = []
        with mock.patch.object(kiosk, "_run", lambda args, timeout=30: ran.append(args) or ""):
            self.assertEqual(kiosk.power("restart"), "")
            self.assertEqual(kiosk.power("shutdown"), "")
            self.assertEqual(kiosk.power("explode"), "Unknown action.")
        self.assertEqual(ran, [["systemctl", "reboot"], ["systemctl", "poweroff"]])


class LeaveTests(StationBase):
    def setUp(self):
        super().setUp()
        self.station.STEP_SECONDS = self.station.READ_SECONDS = 0  # no pauses to read, in tests

    def test_never_during_a_backup_from_the_camera(self):
        self.station._camera_thread = mock.Mock(is_alive=lambda: True)
        with mock.patch.object(kiosk, "power", lambda action: self.fail("must not run")):
            self.assertIn("backup", self.station.leave("shutdown"))

    def test_the_second_copy_is_stopped_first_and_a_failure_carries_on(self):
        self.station.start()
        self.addCleanup(self.station.shutdown)
        order = []
        real = self.station.shutdown

        def shutdown():
            order.append("stopped")
            real()

        with mock.patch.object(self.station, "shutdown", shutdown), \
                mock.patch.object(kiosk, "power", lambda action: order.append(action) or "not allowed"):
            self.assertIn("not allowed", self.station.leave("restart"))
        self.assertEqual(order, ["stopped", "restart"])
        self.assertTrue(self.station._watch_thread.is_alive())  # still running, as before
        self.assertFalse(self.station.quit.is_set())

    def test_unknown(self):
        self.assertEqual(self.station.leave("format"), "Unknown action.")

    def test_each_step_is_listed_with_ok_for_the_switching_screen(self):
        self.station.start()
        with mock.patch.object(kiosk, "power", lambda action: ""):
            self.assertEqual(self.station.leave("restart"), "")
        steps = self.station.state()["leaving"]["steps"]
        self.assertTrue(all(s["state"] == "ok" for s in steps[:-1]))
        self.assertIn("Saved the records", [s["text"] for s in steps])
        self.assertEqual(steps[-1], {"text": "Restarting the tablet", "state": "wait"})
        self.assertIn("This is normal", self.station.state()["leaving"]["note"])

    def test_a_failed_step_is_marked_and_carries_on(self):
        self.station.start()
        self.addCleanup(self.station.shutdown)
        with mock.patch.object(kiosk, "power", lambda action: "not allowed"):
            self.assertIn("not allowed", self.station.leave("restart"))
        leaving = self.station.state()["leaving"]
        self.assertEqual(leaving["steps"][-1]["state"], "fail")
        self.assertEqual(leaving["error"], "not allowed")


class ScreensWatchdogTests(unittest.TestCase):
    def test_firefox_is_opened_again_when_it_closes(self):
        closed = mock.Mock(poll=lambda: 0)
        fresh = mock.Mock(poll=lambda: None)
        box, quitting, opened = {"firefox": closed}, threading.Event(), []

        def reopen(url, kiosk_mode):
            opened.append((url, kiosk_mode))
            return fresh

        with mock.patch.object(server, "open_screens", reopen):
            worker = threading.Thread(target=server._keep_screens_open, args=(box, "http://x/", quitting, 0.01))
            worker.start()
            time.sleep(0.1)
            quitting.set()
            worker.join(2)
        self.assertEqual(opened, [("http://x/", True)])
        self.assertIs(box["firefox"], fresh)


if __name__ == "__main__":
    unittest.main()
