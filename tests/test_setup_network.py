"""Tests for the folder layout, moving data from earlier versions, and the network."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from doubletake import config, migrate, network, settings  # noqa: E402


class HomeBase(unittest.TestCase):
    """A stand-in home folder, with no test library settings in the way."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="doubletake-home-"))
        env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        for key in ("DOUBLETAKE_LIBRARY", "DOUBLETAKE_DATA"):
            os.environ.pop(key, None)
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.home, True)

    def write_config(self, data: dict):
        path = self.home / ".config" / "doubletake" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))


class FolderTests(HomeBase):
    def test_photos_in_pictures_and_records_in_documents(self):
        cfg = config.load()
        self.assertEqual(cfg["library"], str((self.home / "Pictures" / "DoubleTake").resolve()))
        self.assertEqual(cfg["data_dir"], str((self.home / "Documents" / "DoubleTake").resolve()))

    def test_the_desktops_own_folder_names_are_followed(self):
        dirs = self.home / ".config" / "user-dirs.dirs"
        dirs.parent.mkdir(parents=True)
        dirs.write_text('XDG_PICTURES_DIR="$HOME/Bilder"\nXDG_DOCUMENTS_DIR="$HOME/Dokumente"\n')
        cfg = config.load()
        self.assertTrue(cfg["library"].endswith("Bilder/DoubleTake"))
        self.assertTrue(cfg["data_dir"].endswith("Dokumente/DoubleTake"))

    def test_a_library_of_ones_own_keeps_its_records_inside(self):
        self.write_config({"library": "~/Elsewhere"})
        cfg = config.load()
        self.assertEqual(cfg["data_dir"], str((self.home / "Elsewhere" / ".doubletake").resolve()))

    def test_records_from_before_the_rename_are_moved_over(self):
        old = self.home / "Elsewhere" / ".fieldstation"
        old.mkdir(parents=True)
        (old / "ledger.sqlite").write_text("records")
        self.write_config({"library": "~/Elsewhere"})
        cfg = config.load()
        self.assertEqual((Path(cfg["data_dir"]) / "ledger.sqlite").read_text(), "records")
        self.assertFalse(old.exists())

    def test_new_settings_files_name_no_folders(self):
        saved = json.loads(config.write_defaults_if_missing().read_text())
        self.assertNotIn("library", saved)
        self.assertNotIn("data_dir", saved)


class MoveTests(HomeBase):
    def make_old(self):
        old = self.home / "FieldPhotos"
        (old / "2026" / "2026-10-11_test").mkdir(parents=True)
        (old / "2026" / "2026-10-11_test" / "N81_0001.NEF").write_text("photo")
        (old / ".fieldstation" / "logs").mkdir(parents=True)
        (old / ".fieldstation" / "ledger.sqlite").write_text("records")
        return old

    def test_nothing_to_move(self):
        self.assertEqual(migrate.plan().state, "none")

    def test_moving_the_earlier_folder(self):
        self.make_old()
        self.write_config({"library": "~/FieldPhotos", "theme": "night"})
        plan = migrate.plan()
        self.assertEqual(plan.state, "move")
        migrate.move(plan)
        self.assertFalse((self.home / "FieldPhotos").exists())
        photo = self.home / "Pictures" / "DoubleTake" / "2026" / "2026-10-11_test" / "N81_0001.NEF"
        self.assertEqual(photo.read_text(), "photo")
        self.assertEqual((self.home / "Documents" / "DoubleTake" / "ledger.sqlite").read_text(), "records")
        self.assertFalse((self.home / "Pictures" / "DoubleTake" / ".fieldstation").exists())
        self.assertEqual(config.read_user(), {"theme": "night"})
        self.assertEqual(migrate.plan().state, "none")

    def test_never_mixed_into_folders_that_already_have_things(self):
        self.make_old()
        (self.home / "Pictures" / "DoubleTake").mkdir(parents=True)
        (self.home / "Pictures" / "DoubleTake" / "other.jpg").write_text("x")
        plan = migrate.plan()
        self.assertEqual(plan.state, "blocked")
        self.assertTrue((self.home / "FieldPhotos" / ".fieldstation").exists())

    def test_keeping_the_earlier_folder(self):
        self.make_old()
        migrate.keep_old()
        cfg = config.load()
        self.assertEqual(cfg["library"], str((self.home / "FieldPhotos").resolve()))
        self.assertEqual(cfg["data_dir"], str((self.home / "FieldPhotos" / ".fieldstation").resolve()))
        self.assertEqual(migrate.plan().state, "none")

    def test_a_library_of_ones_own_is_left_alone(self):
        self.make_old()
        self.write_config({"library": "~/Elsewhere"})
        self.assertEqual(migrate.plan().state, "none")


class FakeNmcli:  # noqa: D101
    """Answers nmcli's questions the way NetworkManager would, and remembers what it was asked."""

    def __init__(self, devices, wifi_list="", saved="", ap="yes", fail=None):
        self.devices, self.wifi_list, self.saved, self.ap = devices, wifi_list, saved, ap
        self.calls = []
        self.fail = fail or {}

    def __call__(self, args, timeout=15.0):
        self.calls.append(list(args))
        joined = " ".join(args)
        for key, answer in self.fail.items():
            if key in joined:
                return answer
        if "device status" in joined:
            return 0, self.devices, ""
        if "device wifi list" in joined:
            return 0, self.wifi_list, ""
        if "connection show" in joined:
            return 0, self.saved, ""
        if "WIFI-PROPERTIES.AP" in joined:
            return 0, f"WIFI-PROPERTIES.AP:{self.ap}\n", ""
        return 0, "", ""


WIFI_LIST = ("*:Home\\:Net:71:WPA2\n:Home\\:Net:40:WPA2\n:Airport Free:55:\n:Hangar 5:30:WPA2\n:::\n")


class NetworkTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(network, "nmcli_path", return_value="/usr/bin/nmcli")
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake(self, **kw):
        fake = FakeNmcli(**kw)
        patcher = mock.patch.object(network, "_run", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def test_reading_nmcli_lines(self):
        self.assertEqual(network.split("yes:My\\:Net:70:WPA2"), ["yes", "My:Net", "70", "WPA2"])
        self.assertEqual(network.split("a\\\\b:"), ["a\\b", ""])

    def test_networks_listed_once_each_strongest_first(self):
        nets = network.parse_networks(WIFI_LIST)
        self.assertEqual([n["ssid"] for n in nets], ["Home:Net", "Airport Free", "Hangar 5"])
        self.assertEqual((nets[0]["in_use"], nets[0]["signal"], nets[1]["secure"]), (True, 71, False))

    def test_status_on_wifi_and_cable(self):
        self.fake(devices="wlp1s0:wifi:connected:Home\\:Net\nenp0s20u1:ethernet:connected:Wired connection 1\n"
                          "lo:loopback:connected (externally):lo\n", wifi_list=WIFI_LIST)
        st = network.status()
        self.assertEqual((st["ssid"], st["signal"], st["wired"], st["hotspot"]), ("Home:Net", 71, True, False))

    def test_status_with_the_hotspot_on(self):
        self.fake(devices=f"wlp1s0:wifi:connected:{network.HOTSPOT}\n")
        st = network.status()
        self.assertEqual((st["hotspot"], st["ssid"], st["wired"]), (True, "", False))

    def test_status_without_nmcli(self):
        with mock.patch.object(network, "nmcli_path", return_value=None):
            self.assertEqual(network.status(), {"available": False})

    def test_joining_a_new_and_a_saved_network(self):
        fake = self.fake(devices="wlp1s0:wifi:disconnected:\n", saved="Hangar 5:802-11-wireless\n")
        self.assertEqual(network.connect("Airport Free", "secret12"), "")
        self.assertIn(["device", "wifi", "connect", "Airport Free", "ifname", "wlp1s0", "password", "secret12"],
                      fake.calls)
        self.assertEqual(network.connect("Hangar 5"), "")
        self.assertIn(["connection", "up", "id", "Hangar 5"], fake.calls)

    def test_a_wrong_password_says_so(self):
        self.fake(devices="wlp1s0:wifi:disconnected:\n",
                  fail={"wifi connect": (4, "", "Error: Connection activation failed: Secrets were required")})
        self.assertEqual(network.connect("Hangar 5", "wrongpass"), "The password was not accepted.")

    def test_hotspot_on_and_off(self):
        fake = self.fake(devices="wlp1s0:wifi:connected:Home\n")
        self.assertEqual(network.hotspot_on("DoubleTake", "abcdefgh23"), "")
        self.assertIn(["device", "wifi", "hotspot", "ifname", "wlp1s0", "con-name", network.HOTSPOT,
                       "ssid", "DoubleTake", "password", "abcdefgh23"], fake.calls)
        fake.devices = "wlp1s0:wifi:disconnected:\n"
        fake.wifi_list = WIFI_LIST
        fake.saved = "Home\\:Net:802-11-wireless\n" + network.HOTSPOT + ":802-11-wireless\n"
        self.assertEqual(network.hotspot_off(), "")
        self.assertIn(["connection", "down", "id", network.HOTSPOT], fake.calls)
        self.assertIn(["connection", "delete", "id", network.HOTSPOT], fake.calls)
        # Back onto the known network, and never by asking for "the best one", which can be the hotspot.
        self.assertIn(["connection", "up", "id", "Home:Net"], fake.calls)
        self.assertNotIn(["device", "connect", "wlp1s0"], fake.calls)

    def test_a_hotspot_that_will_not_stop_is_reported(self):
        self.fake(devices=f"wlp1s0:wifi:connected:{network.HOTSPOT}\n")
        self.assertIn("did not stop", network.hotspot_off())

    def test_a_card_that_cannot_host_is_told_plainly(self):
        self.fake(devices="wlp1s0:wifi:connected:Home\n", ap="no")
        self.assertIn("cannot run a hotspot", network.hotspot_on("DoubleTake", "abcdefgh23"))

    def test_passwords(self):
        password = network.new_password()
        self.assertEqual(len(password), 10)
        self.assertFalse(set(password) & set("01lIoO"))


class HotspotSettingTests(HomeBase):
    def test_name_and_password_are_checked(self):
        cfg = config.load()
        self.assertEqual(settings.apply(cfg, {"hotspot_name": "My tablet", "hotspot_password": "longenough"}), "")
        self.assertEqual(config.read_user()["hotspot_password"], "longenough")
        self.assertIn("Cannot set", settings.apply(cfg, {"hotspot_password": "short"}))
        self.assertIn("Cannot set", settings.apply(cfg, {"hotspot_name": ""}))


if __name__ == "__main__":
    unittest.main()
