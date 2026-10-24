"""Tests for the NAS's trusted networks, card formatting and factory reset."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from fakes import make_card  # noqa: E402
from test_engine import Base  # noqa: E402

from doubletake import cards, config, device, nas  # noqa: E402
from doubletake.mirror import run_mirror  # noqa: E402


class HelperCalls:
    def __init__(self):
        self.calls = []
        self.answer = {"ok": True}

    def __call__(self, *args, stdin=None, timeout=300):
        self.calls.append(list(args))
        return dict(self.answer)


def net(name, kind="wifi", zone="", secured=True, uuid="1b2c3d4e-0000-1111-2222-333344445555", dev="wlp1s0"):
    return {"name": name, "uuid": uuid, "device": dev, "kind": kind, "zone": zone, "secured": secured}


class ReachTests(unittest.TestCase):
    """The screens warn when the share is on but the network the tablet is on keeps it closed."""

    def addrs(self):
        return [
            {"device": "enp0s1", "address": "192.168.1.20", "kind": "cable", "network": "Wired"},
            {"device": "wlp1s0", "address": "192.168.1.21", "kind": "wifi", "network": "Home"},
            {"device": "wlp2s0", "address": "10.0.0.5", "kind": "wifi", "network": "Open Cafe"},
            {"device": "virbr0", "address": "192.168.122.1", "kind": "other", "network": "virbr0"},
        ]

    def test_marks_each_network(self):
        nas._SECURED.clear()
        with mock.patch.object(nas, "_secured", lambda name: name != "Open Cafe"):
            found = nas.mark_reach(self.addrs(), {"nas_cable": True, "nas_trusted": ["Open Cafe"]})
        self.assertEqual([a["device"] for a in found], ["enp0s1", "wlp1s0", "wlp2s0"])
        self.assertEqual([a["open"] for a in found], [True, False, False])
        self.assertFalse(found[2]["secured"])

    def test_trusted_home_is_open(self):
        with mock.patch.object(nas, "_secured", lambda name: True):
            found = nas.mark_reach(self.addrs()[1:2], {"nas_trusted": ["Home"]})
        self.assertTrue(found[0]["open"])

    def test_cable_switched_off(self):
        found = nas.mark_reach(self.addrs()[:1], {"nas_cable": False})
        self.assertFalse(found[0]["open"])


class TrustTests(unittest.TestCase):
    def setUp(self):
        self.calls = HelperCalls()
        patcher = mock.patch.object(nas, "helper", self.calls)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_which_zone_each_network_belongs_in(self):
        cfg = {"nas_cable": True, "nas_trusted": ["Home Wi-Fi", "Open Cafe"]}
        self.assertEqual(nas.wanted_zone(net("Wired connection 1", "cable"), cfg), "home")
        self.assertEqual(nas.wanted_zone(net("Home Wi-Fi"), cfg), "home")
        self.assertEqual(nas.wanted_zone(net("Hotel Guest"), cfg), "public")
        self.assertEqual(nas.wanted_zone(net("Open Cafe", secured=False), cfg), "public")  # never an open network
        cfg["nas_cable"] = False
        self.assertEqual(nas.wanted_zone(net("Wired connection 1", "cable"), cfg), "public")

    def test_only_networks_in_the_wrong_zone_are_changed(self):
        cfg = {"nas_cable": True, "nas_trusted": ["Home Wi-Fi"]}
        nets = [net("Home Wi-Fi", zone=""), net("Hotel", zone="", uuid="2" * 8 + "-0000-1111-2222-333344445555"),
                net("Old trusted", zone="home", uuid="3" * 8 + "-0000-1111-2222-333344445555"),
                net("Wired connection 1", "cable", zone="home", uuid="4" * 8 + "-0000-1111-2222-333344445555",
                    dev="enp0s20u1")]
        self.assertEqual(nas.sync_zones(cfg, nets), [])
        self.assertEqual(self.calls.calls, [
            ["zone", nets[0]["uuid"], "home", "wlp1s0"],
            ["zone", nets[2]["uuid"], "public", "wlp1s0"],
        ])

    def test_helper_problems_are_reported(self):
        self.calls.answer = {"ok": False, "error": "refused"}
        problems = nas.sync_zones({"nas_trusted": ["Home"]}, [net("Home")])
        self.assertEqual(problems, ["Home: refused"])

    def test_the_helper_missing_says_how_to_fix_it(self):
        mock.patch.stopall()
        with mock.patch.object(nas, "HELPER", "/nonexistent/helper"):
            self.assertIn("doubletake setup", nas.helper("nas", "on")["error"])


class CardFormatTests(Base):
    def setUp(self):
        super().setUp()
        os.environ.pop("DOUBLETAKE_CARD", None)
        self.sd = self.tmp / "SD_CARD"
        self.sd.mkdir()
        (self.sd / "holiday.jpg").write_text("x")
        self.cfg["second_copy"] = str(self.sd)
        make_card(self.card, count=3)
        self.backup()
        self.sdcard = cards.detect(self.cfg, self.ledger).card
        cards.tag(self.sdcard, self.ledger)
        run_mirror(self.cfg, self.ledger, self.sdcard)
        self.calls = HelperCalls()
        patcher = mock.patch.object(nas, "helper", self.calls)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_folder_is_never_formatted(self):
        self.assertIn("folder", device.format_card(self.cfg, self.ledger)["error"])
        self.assertEqual(self.calls.calls, [])

    def test_formatting_a_card_forgets_its_copies(self):
        self.sdcard.device = "/dev/mmcblk0p1"
        self.sdcard.label = "SD_CARD"
        with mock.patch.object(device, "_card", return_value=(self.sdcard, "")), \
                mock.patch.object(device.subprocess, "run") as run:
            report = device.card_report(self.cfg, self.ledger)
            self.assertEqual((report["held"], report["unprotected"], report["other_files"]), (3, 3, 1))
            done = device.format_card(self.cfg, self.ledger)
        self.assertEqual(done, {"error": "", "label": "SD_CARD"})
        self.assertEqual(self.calls.calls, [["format-card", "/dev/mmcblk0p1", "SD_CARD"]])
        self.assertIn("udisksctl", run.call_args[0][0])
        self.assertEqual(self.ledger.copy_totals(self.sdcard.card_id)[0], 0)
        self.assertIsNone(self.ledger.card_state(f"id:{self.sdcard.card_id}"))


class ResetTests(Base):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")})
        env.start()
        self.addCleanup(env.stop)
        self.library = self.home / "Pictures" / "DoubleTake"
        self.records = self.home / "Documents" / "DoubleTake"
        self.library.mkdir(parents=True)
        self.cfg["library"] = str(self.library)
        self.cfg["data_dir"] = str(self.records)
        self.ledger.close()
        from doubletake.ledger import Ledger
        self.ledger = Ledger(self.library, self.records)
        make_card(self.card, count=2)
        self.backup()
        self.installed = mock.patch.object(nas, "installed", return_value=False)
        self.installed.start()
        self.addCleanup(self.installed.stop)

    def test_reset_settings_keeps_photos_and_where_they_are(self):
        config.save({"theme": "night", "library": str(self.library), "data_dir": str(self.records)})
        self.cfg["theme"] = "night"
        self.assertEqual(device.reset_settings(self.cfg), {"error": ""})
        self.assertEqual(config.read_user(), {"library": str(self.library), "data_dir": str(self.records)})
        self.assertEqual(self.cfg["theme"], "daylight")
        self.assertTrue(any(self.library.rglob("*.NEF")))

    def test_erasing_asks_again_while_shoots_are_not_imported(self):
        self.ledger.close()
        first = device.erase_everything(self.cfg, self.library, self.records, confirmed=False)
        self.assertEqual((first["needs_confirm"], first["unimported"]), (True, 1))
        self.assertTrue(any(self.library.rglob("*.NEF")))
        done = device.erase_everything(self.cfg, self.library, self.records, confirmed=True)
        self.assertEqual(done, {"error": "", "erased": True})
        self.assertFalse(any(self.library.rglob("*.NEF")))
        self.assertFalse(self.records.exists())
        self.assertTrue(self.library.is_dir())

    def test_nothing_outside_home_is_erased(self):
        self.ledger.close()
        result = device.erase_everything(self.cfg, Path("/"), self.records, confirmed=True)
        self.assertIn("may erase", result["error"])
        result = device.erase_everything(self.cfg, self.home, self.records, confirmed=True)
        self.assertIn("may erase", result["error"])
        self.assertTrue(any(self.library.rglob("*.NEF")))


if __name__ == "__main__":
    unittest.main()


class InstalledTests(unittest.TestCase):
    def test_a_folder_the_screens_cannot_read_is_not_a_crash(self):
        from doubletake import nas

        def refuse(self):
            raise PermissionError(13, "Permission denied", str(self))

        with mock.patch.object(nas.Path, "is_file", refuse):
            self.assertFalse(nas.installed())


class ComputerNameTests(unittest.TestCase):
    def setUp(self):
        from doubletake import nas
        self.nas = nas
        nas._names.clear()

    def test_a_mac_by_its_name(self):
        out = "(2, 1, 1, '2605:a601:90a4:c100::93', 'Nicks-MacBook-Pro.local', uint32 4)\n"
        with mock.patch.object(self.nas.shutil, "which", lambda n: "/usr/bin/gdbus"), \
                mock.patch.object(self.nas.subprocess, "run", lambda *a, **k: mock.Mock(stdout=out)):
            self.nas._ask("2605:a601:90a4:c100::93")
        self.assertEqual(self.nas.computer_name("2605:a601:90a4:c100::93"), "Nicks-MacBook-Pro")

    def test_no_answer_keeps_the_address(self):
        with mock.patch.object(self.nas.shutil, "which", lambda n: None), \
                mock.patch.object(self.nas.socket, "gethostbyaddr", mock.Mock(side_effect=OSError)):
            self.nas._ask("192.168.1.167")
        self.assertEqual(self.nas._names["192.168.1.167"][0], "")

    def test_router_names_are_tidied(self):
        with mock.patch.object(self.nas.shutil, "which", lambda n: None), \
                mock.patch.object(self.nas.socket, "gethostbyaddr", lambda a: ("studio-imac.lan", [], [a])):
            self.nas._ask("192.168.1.20")
        self.assertEqual(self.nas._names["192.168.1.20"][0], "studio-imac")

    def test_addresses(self):
        self.assertTrue(self.nas.is_address("ipv4:192.168.1.5:445"))
        self.assertTrue(self.nas.is_address("fe80::1%enp0s1"))
        self.assertFalse(self.nas.is_address("Nicks-MacBook-Pro"))


class MergeTests(unittest.TestCase):
    def test_one_mac_on_the_share_and_sftp_is_one_computer(self):
        from doubletake import nas
        rows = nas.merge_sessions([
            {"machine": "Nicks-MacBook-Pro", "address": "2605::93", "kind": "smb", "user": "photos"},
            {"machine": "Nicks-MacBook-Pro", "address": "192.168.1.167", "kind": "sftp", "user": ""},
            {"machine": "192.168.1.20", "address": "192.168.1.20", "kind": "smb", "user": "photos"}])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["kind"], "both")


class SameCardTests(unittest.TestCase):
    NEIGH = ("192.168.1.167 dev enp0s1 lladdr 3c:22:fb:aa:bb:cc REACHABLE\n"
             "2605:a601:90a4:c100:d4de:ab6f:93aa:1 dev enp0s1 lladdr 3c:22:fb:aa:bb:cc STALE\n"
             "192.168.1.1 dev enp0s1 lladdr 00:11:22:33:44:55 REACHABLE\n"
             "192.168.1.99 dev enp0s1 FAILED\n")

    def test_the_ipv6_address_takes_the_name_of_the_ipv4_one(self):
        from doubletake import nas
        nas._names.clear()
        nas._names["192.168.1.167"] = ("MacBookPro", 9e18)
        nas._names["2605:a601:90a4:c100:d4de:ab6f:93aa:1"] = ("", 9e18)
        cards = nas.neighbours(self.NEIGH)
        self.assertNotIn("192.168.1.99", cards)
        self.assertEqual(nas._name_by_card("2605:a601:90a4:c100:d4de:ab6f:93aa:1", cards), "MacBookPro")
        rows = nas.merge_sessions([{"machine": "MacBookPro", "kind": "smb"}, {"machine": "MacBookPro", "kind": "sftp"}])
        self.assertEqual((len(rows), rows[0]["kind"]), (1, "both"))
