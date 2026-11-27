"""The first start after installing the package."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doubletake import firstrun, kiosk, maintenance, settings  # noqa: E402


class FirstRunTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="doubletake-first-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        for p in (mock.patch.object(firstrun, "packaged", lambda: True),
                  mock.patch.object(settings, "AUTOSTART", self.home / ".config/autostart/doubletake.desktop"),
                  mock.patch.object(kiosk, "SCRIPT", self.home / ".local/bin/gnome-kiosk-script"),
                  mock.patch.object(maintenance, "AUTOSTART", self.home / ".config/autostart/doubletake-maintenance.desktop"),
                  mock.patch.object(firstrun.shutil, "which", lambda name: "/usr/bin/" + name)):
            p.start()
            self.addCleanup(p.stop)

    def run_nothing(self, args):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_an_earlier_setup_sh_install_is_tidied_away(self):
        cmd = self.home / ".local/bin/doubletake"
        cmd.parent.mkdir(parents=True)
        cmd.write_text("#!/usr/bin/env bash\n# Installed by setup.sh. Runs DoubleTake from ~/DoubleTake\n")
        entry = self.home / ".local/share/applications/doubletake.desktop"
        entry.parent.mkdir(parents=True)
        entry.write_text(f"[Desktop Entry]\nExec={cmd} gui\n")
        (self.home / "DoubleTake/doubletake").mkdir(parents=True)
        (self.home / "DoubleTake/doubletake/__init__.py").write_text("")
        (self.home / "DoubleTake/run.py").write_text("")
        settings.AUTOSTART.parent.mkdir(parents=True)
        settings.AUTOSTART.write_text(f"Exec={cmd} gui --autostart\n")
        with mock.patch.object(settings.shutil, "which", lambda name: "/usr/bin/doubletake"):
            done = firstrun.user_setup(self.home, run=self.run_nothing)
        self.assertFalse(cmd.exists())
        self.assertFalse(entry.exists())
        self.assertFalse((self.home / "DoubleTake").exists())
        self.assertIn("/usr/bin/doubletake gui --autostart", settings.AUTOSTART.read_text())
        self.assertEqual(len(done), 3)

    def test_someone_elses_files_are_left_alone(self):
        cmd = self.home / ".local/bin/doubletake"
        cmd.parent.mkdir(parents=True)
        cmd.write_text("#!/bin/sh\necho mine\n")
        (self.home / "DoubleTake").mkdir()
        (self.home / "DoubleTake/notes.txt").write_text("mine")
        self.assertEqual(firstrun.user_setup(self.home, run=self.run_nothing), [])
        self.assertTrue(cmd.exists())
        self.assertTrue((self.home / "DoubleTake/notes.txt").exists())

    def test_the_system_setup_runs_the_helper_through_sudo(self):
        ran = []
        def run(args):
            ran.append(args)
            return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True}) + "\n", stderr="")
        self.assertEqual(firstrun.system_setup(str(self.home / "Pictures/DoubleTake"), run=run), "")
        self.assertEqual(ran[0][:4], ["sudo", "-n", "/usr/libexec/doubletake-helper", "adopt"])

    def test_no_password_given_changes_nothing(self):
        run = lambda args: SimpleNamespace(returncode=1, stdout="", stderr="a password is required")
        self.assertIn("Nothing was changed", firstrun.system_setup(str(self.home / "P"), run=run))



class ApplianceSetupTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="doubletake-appl-")
        self.addCleanup(shutil.rmtree, self.home, True)
        p = mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": self.home})
        p.start()
        self.addCleanup(p.stop)

    def setup(self):
        from doubletake import cli, config, poweron

        with mock.patch.object(poweron, "switch", lambda *a: ""), mock.patch.object(kiosk, "switch", lambda on: ""):
            self.assertEqual(cli.cmd_appliance_setup(SimpleNamespace(), config.load()), 0)
        return config.load()

    def test_an_appliance_hotspot_is_named_doubletake_hs(self):
        cfg = self.setup()
        self.assertEqual(cfg["hotspot_name"], "DoubleTake_HS")
        self.assertTrue(cfg["welcome"])

    def test_a_hotspot_name_already_chosen_is_kept(self):
        from doubletake import config

        config.save({"hotspot_name": "Nick's tablet"})
        self.assertEqual(self.setup()["hotspot_name"], "Nick's tablet")


if __name__ == "__main__":
    unittest.main()
