"""Tests for moving over from the name Field Station, used before 0.8.1."""

from __future__ import annotations

import json
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

from doubletake import config, kiosk, maintenance, rename, settings  # noqa: E402


class RenameTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="doubletake-rename-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.favorites = ["firefox.desktop", "fieldstation-restart.desktop"]
        patches = [
            mock.patch.dict(os.environ, {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")}),
            mock.patch.object(settings, "AUTOSTART", self.home / ".config/autostart/doubletake.desktop"),
            mock.patch.object(kiosk, "SCRIPT", self.home / ".local/bin/gnome-kiosk-script"),
            mock.patch.object(maintenance, "AUTOSTART", self.home / ".config/autostart/doubletake-maintenance.desktop"),
            mock.patch.object(maintenance, "_favorites", lambda: list(self.favorites)),
            mock.patch.object(maintenance, "_set_favorites", lambda apps: setattr(self, "favorites", list(apps))),
            mock.patch.object(rename.shutil, "which", lambda name: None),  # no nmcli here
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def put(self, rel, text="x"):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_photos_records_and_settings_move_over(self):
        self.put("Pictures/FieldStation/2026/2026-10-13_01/DSC_0001.NEF", "raw")
        self.put("Documents/FieldStation/ledger.sqlite", "records")
        self.put(".config/doubletake/config.json", json.dumps({"hotspot_name": "FieldStation", "look": "cockpit"}))
        notes = rename.run()
        self.assertEqual((self.home / "Pictures/DoubleTake/2026/2026-10-13_01/DSC_0001.NEF").read_text(), "raw")
        self.assertEqual((self.home / "Documents/DoubleTake/ledger.sqlite").read_text(), "records")
        self.assertFalse((self.home / "Pictures/FieldStation").exists())
        user = config.read_user()
        self.assertEqual((user["hotspot_name"], user["look"]), ("DoubleTake", "cockpit"))
        self.assertTrue(any("Photos moved" in n for n in notes))
        self.assertEqual(rename.run(), [])  # again: nothing left to do

    def test_a_folder_of_their_own_stays(self):
        mine = self.put("Shoots/2026/x.NEF").parent.parent
        self.put("Pictures/FieldStation/old.NEF")
        self.put(".config/doubletake/config.json", json.dumps({"library": str(mine)}))
        rename.run()
        self.assertTrue((self.home / "Pictures/FieldStation/old.NEF").exists())
        self.assertEqual(config.read_user()["library"], str(mine))

    def test_the_old_default_named_in_the_settings_is_let_go(self):
        self.put("Pictures/FieldStation/a.NEF")
        self.put(".config/doubletake/config.json", json.dumps({"library": str(self.home / "Pictures/FieldStation")}))
        rename.run()
        self.assertNotIn("library", config.read_user())
        self.assertTrue((self.home / "Pictures/DoubleTake/a.NEF").exists())

    def test_never_over_photos_already_there(self):
        self.put("Pictures/FieldStation/a.NEF")
        self.put("Pictures/DoubleTake/b.NEF")
        notes = rename.run()
        self.assertTrue((self.home / "Pictures/FieldStation/a.NEF").exists())
        self.assertTrue(any(n.startswith("NOT MOVED") for n in notes))

    def test_desktop_and_kiosk_move_over(self):
        self.put(".config/autostart/fieldstation.desktop", "Exec=/x/fieldstation gui --autostart")
        self.put(".local/share/applications/fieldstation.desktop")
        self.put(".local/bin/fieldstation")
        kiosk.SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        kiosk.SCRIPT.write_text("#!/bin/sh\n# Written by Field Station. The kiosk...\nexec fieldstation gui\n")
        rename.run()
        self.assertTrue(settings.AUTOSTART.is_file())
        self.assertIn("doubletake", settings.AUTOSTART.read_text())
        self.assertFalse((self.home / ".config/autostart/fieldstation.desktop").exists())
        self.assertFalse((self.home / ".local/share/applications/fieldstation.desktop").exists())
        self.assertFalse((self.home / ".local/bin/fieldstation").exists())
        text = kiosk.SCRIPT.read_text()
        self.assertIn(kiosk.MARK, text)
        self.assertIn("doubletake", text.split("exec", 1)[1])
        self.assertFalse(kiosk.SCRIPT.with_name("gnome-kiosk-script.before-doubletake").exists())  # ours, not kept
        self.assertEqual(self.favorites, ["firefox.desktop", maintenance.RESTART_APP])

    def test_old_network_connections_removed(self):
        ran = []
        with mock.patch.object(rename.shutil, "which", lambda name: "/usr/bin/nmcli"):
            rename.connections([], run=lambda args, **k: ran.append(args) or SimpleNamespace(returncode=0))
        self.assertEqual([a[-1] for a in ran], ["FieldStation-Direct", "FieldStation-Hotspot"])


if __name__ == "__main__":
    unittest.main()
