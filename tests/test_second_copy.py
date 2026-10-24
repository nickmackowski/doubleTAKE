"""Tests for the card detection, the second copy and the Safe to Format rule."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from fakes import make_card  # noqa: E402
from test_engine import Base, sha  # noqa: E402

from doubletake import cards, mirror  # noqa: E402
from doubletake.ingest import coverage, make_plan  # noqa: E402
from doubletake.mirror import run_mirror, verify_card  # noqa: E402
from doubletake.sources.folder import FolderSource  # noqa: E402

# How the 5175's own slot shows a card, taken from the real tablet.
MOUNTINFO = (
    "96 1 253:0 / / rw,relatime shared:1 - xfs /dev/mapper/rhel-root rw,seclabel\n"
    "120 96 253:2 / /home rw,relatime shared:60 - xfs /dev/mapper/rhel-home rw,seclabel\n"
    "510 96 179:1 / {media}/fieldtest/SD_CARD rw,nosuid,nodev,relatime shared:300 - exfat /dev/mmcblk0p1 "
    "rw,uid=1000,gid=1000,fmask=0022,dmask=0022,iocharset=utf8,errors=remount-ro\n"
    "511 96 8:17 / {media}/fieldtest/USB\\040STICK ro,nosuid,nodev shared:301 - vfat /dev/sdb1 ro,uid=1000\n"
    "512 96 11:0 / {media}/fieldtest/DVD ro shared:302 - iso9660 /dev/sr0 ro\n"
)


class DetectionTests(Base):
    def setUp(self):
        super().setUp()
        self.media = self.tmp / "media"
        for name in ("SD_CARD", "USB STICK", "DVD"):
            (self.media / "fieldtest" / name).mkdir(parents=True)
        self.text = MOUNTINFO.replace("{media}", str(self.media))

    def test_reads_the_systems_list_of_opened_drives(self):
        mounts = cards.parse_mountinfo(self.text)
        self.assertEqual(len(mounts), 5)
        self.assertTrue(mounts[3]["mount"].endswith("USB STICK"))
        self.assertIn("ro", mounts[3]["options"])
        self.assertNotIn("ro", mounts[2]["options"])  # errors=remount-ro is not read-only

    def test_finds_the_card_in_the_tablets_slot(self):
        found = cards.candidates("DoubleTake", None, self.text, roots=(str(self.media) + "/",))
        self.assertEqual([c.label for c in found], ["SD_CARD", "USB STICK"])  # the disc is left out
        card = found[0]
        self.assertTrue(card.is_mmc)
        self.assertEqual(card.fstype, "exfat")
        self.assertFalse(card.read_only)
        self.assertTrue(found[1].read_only)
        self.assertEqual(card.root, self.media / "fieldtest" / "SD_CARD" / "DoubleTake")

    def test_a_card_that_is_present_but_not_opened_is_noticed(self):
        sys_block = self.tmp / "sys"
        for name, size in (("mmcblk0", "1000000"), ("mmcblk0boot0", "8192"), ("nvme0n1", "9999")):
            (sys_block / name).mkdir(parents=True)
            (sys_block / name / "size").write_text(size + "\n")
        self.assertEqual(cards.unopened_devices("", str(sys_block)), ["mmcblk0, 512.0 MB"])
        self.assertEqual(cards.unopened_devices(self.text, str(sys_block)), [])


class CardChoiceTests(Base):
    def setUp(self):
        super().setUp()
        self.sd = self.tmp / "SD_CARD"
        self.sd.mkdir()
        self.cfg["second_copy"] = str(self.sd)
        os.environ.pop("DOUBLETAKE_CARD", None)

    def test_new_card_then_tagged_then_recognised(self):
        found = cards.detect(self.cfg, self.ledger)
        self.assertEqual(found.state, "new")
        cards.tag(found.card, self.ledger)
        again = cards.detect(self.cfg, self.ledger)
        self.assertEqual(again.state, "ready")
        self.assertEqual(again.card.card_id, found.card.card_id)
        marker = json.loads((self.sd / "DoubleTake" / ".doubletake" / "card.json").read_text())
        self.assertEqual(marker["card_id"], found.card.card_id)

    def test_declining_a_card_writes_nothing_to_it(self):
        found = cards.detect(self.cfg, self.ledger)
        cards.decline(found.card, self.ledger)
        self.assertEqual(list(self.sd.iterdir()), [])
        self.assertEqual(cards.detect(self.cfg, self.ledger).state, "declined")
        cards.allow(found.card, self.ledger)
        self.assertEqual(cards.detect(self.cfg, self.ledger).state, "new")

    def test_off_and_missing(self):
        self.cfg["second_copy"] = "off"
        self.assertEqual(cards.detect(self.cfg, self.ledger).state, "off")
        self.cfg["second_copy"] = str(self.tmp / "nowhere")
        found = cards.detect(self.cfg, self.ledger)
        self.assertEqual((found.state, found.card), ("none", None))

    def test_two_untagged_drives_are_not_guessed_between(self):
        a, b = self.tmp / "m" / "A", self.tmp / "m" / "B"
        a.mkdir(parents=True)
        b.mkdir()
        text = f"1 0 8:1 / {a} rw - vfat /dev/sdb1 rw\n2 0 8:2 / {b} rw - vfat /dev/sdc1 rw\n"
        self.cfg["second_copy"] = "auto"
        self.cfg["second_copy_device"] = "usb"
        real = cards.candidates

        def two_drives(folder, library, mountinfo=None):
            return real(folder, None, text, roots=(str(self.tmp / "m") + "/",))

        with mock.patch.object(cards, "candidates", two_drives):
            found = cards.detect(self.cfg, self.ledger)
            self.assertEqual(found.state, "several")
            # Tagging one settles it.
            cards.tag(found.all[0], self.ledger)
            self.assertEqual(cards.detect(self.cfg, self.ledger).state, "ready")


    def test_only_the_chosen_kind_of_drive_is_used(self):
        sd, stick = self.tmp / "m" / "SD", self.tmp / "m" / "STICK"
        sd.mkdir(parents=True)
        stick.mkdir()
        text = f"1 0 179:1 / {sd} rw - exfat /dev/mmcblk0p1 rw\n2 0 8:1 / {stick} rw - exfat /dev/sdb1 rw\n"
        self.cfg["second_copy"] = "auto"
        real = cards.candidates

        def both(folder, library, mountinfo=None):
            return real(folder, None, text, roots=(str(self.tmp / "m") + "/",))

        with mock.patch.object(cards, "candidates", both):
            found = cards.detect(self.cfg, self.ledger)  # microSD by default
            self.assertEqual((found.state, found.card.mount, found.card.noun, found.kind), ("new", sd, "microSD card", "microsd"))
            self.assertEqual(found.other_kind, 1)
            self.cfg["second_copy_device"] = "usb"
            found = cards.detect(self.cfg, self.ledger)
            self.assertEqual((found.state, found.card.mount, found.card.noun, found.kind), ("new", stick, "USB drive", "usb"))
            # A tagged microSD card is not used once the USB drive is chosen.
            cards.tag(cards.card_at(sd, "DoubleTake", "/dev/mmcblk0p1", "exfat", set()), self.ledger)
            self.assertEqual(cards.detect(self.cfg, self.ledger).card.mount, stick)

        def card_only(folder, library, mountinfo=None):
            return real(folder, None, text.splitlines()[0] + "\n", roots=(str(self.tmp / "m") + "/",))

        with mock.patch.object(cards, "candidates", card_only), \
                mock.patch.object(cards, "unopened_devices", lambda *a, **k: []):
            found = cards.detect(self.cfg, self.ledger)  # the USB drive is not plugged in
            self.assertEqual((found.state, found.kind, found.other_kind), ("none", "usb", 1))


class OldLayoutTests(Base):
    """A card prepared before 0.9.5: FieldPhotos/.fieldstation, moved over when seen."""

    def setUp(self):
        super().setUp()
        self.sd = self.tmp / "OLD_CARD"
        old = self.sd / "FieldPhotos"
        (old / ".fieldstation").mkdir(parents=True)
        (old / ".fieldstation" / "card.json").write_text(json.dumps({"card_id": "abc123"}))
        (old / "2026" / "2026-10-11_1407").mkdir(parents=True)
        (old / "2026" / "2026-10-11_1407" / "DSC_0001.NEF").write_text("photo")

    def test_the_folders_are_renamed_and_the_card_is_still_known(self):
        card = cards.card_at(self.sd)
        self.assertEqual(card.card_id, "abc123")
        self.assertEqual(sorted(p.name for p in self.sd.iterdir()), ["DoubleTake"])
        self.assertTrue((self.sd / "DoubleTake" / ".doubletake" / "card.json").is_file())
        self.assertEqual((self.sd / "DoubleTake" / "2026" / "2026-10-11_1407" / "DSC_0001.NEF").read_text(), "photo")

    def test_a_read_only_card_is_left_alone_but_still_known(self):
        card = cards.card_at(self.sd, options={"ro"})
        self.assertEqual(card.card_id, "abc123")
        self.assertTrue((self.sd / "FieldPhotos").is_dir())

    def test_an_old_settings_value_follows_the_new_name(self):
        from doubletake import config

        with mock.patch.object(config, "config_path", return_value=self.tmp / "cfg.json"):
            (self.tmp / "cfg.json").write_text(json.dumps({"second_copy_folder": "FieldPhotos"}))
            self.assertEqual(config.load()["second_copy_folder"], "DoubleTake")


class MirrorTests(Base):
    def setUp(self):
        super().setUp()
        self.sd = self.tmp / "SD_CARD"
        self.sd.mkdir()
        (self.sd / "holiday.jpg").write_bytes(b"someone else's file")
        (self.sd / "DCIM").mkdir()
        (self.sd / "DCIM" / "old.NEF").write_bytes(b"left alone")
        self.cfg["second_copy"] = str(self.sd)
        os.environ.pop("DOUBLETAKE_CARD", None)
        make_card(self.card, count=5)
        self.result = self.backup()
        self.sdcard = cards.detect(self.cfg, self.ledger).card
        cards.tag(self.sdcard, self.ledger)

    def plan(self):
        with FolderSource(str(self.card)) as source:
            return make_plan(source, self.ledger)

    def on_card(self):
        shoot = Path(self.result.shoot_folder).relative_to(self.library)
        return self.sd / "DoubleTake" / shoot

    def test_mirror_copies_checks_and_records(self):
        self.assertFalse(coverage(self.plan(), self.ledger).safe_to_format)
        res = run_mirror(self.cfg, self.ledger, self.sdcard)
        self.assertEqual((res.verified, res.failed, res.left, res.interrupted), (5, 0, 0, False))
        for name in self.shoot_files(self.result):
            if name.endswith(".NEF"):
                self.assertEqual(sha(self.on_card() / name), sha(Path(self.result.shoot_folder) / name))
        report = json.loads((self.on_card() / "manifest.json").read_text())
        self.assertEqual(report["second_copies"][0]["files_verified"], 5)
        cov = coverage(self.plan(), self.ledger)
        self.assertEqual((cov.photos, cov.on_tablet, cov.on_second), (5, 5, 5))
        self.assertTrue(cov.safe_to_format)
        self.assertEqual(self.ledger.list_shoots()[0]["mirrored"], 5)
        # A second run has nothing to do.
        self.assertEqual(run_mirror(self.cfg, self.ledger, self.sdcard).wanted, 0)

    def test_nothing_else_on_the_card_is_touched(self):
        run_mirror(self.cfg, self.ledger, self.sdcard)
        self.assertEqual(sorted(p.name for p in self.sd.iterdir()), ["DCIM", "DoubleTake", "holiday.jpg"])
        self.assertEqual((self.sd / "holiday.jpg").read_bytes(), b"someone else's file")
        self.assertEqual((self.sd / "DCIM" / "old.NEF").read_bytes(), b"left alone")
        leftovers = [p for p in (self.sd / "DoubleTake").rglob("*") if p.name.endswith((".part", ".tmp"))]
        self.assertEqual(leftovers, [])

    def test_not_safe_while_photos_are_still_only_on_the_camera(self):
        run_mirror(self.cfg, self.ledger, self.sdcard)
        make_card(self.card, count=2, start=9000)
        cov = coverage(self.plan(), self.ledger)
        self.assertEqual((cov.photos, cov.on_tablet, cov.on_second), (7, 5, 5))
        self.assertFalse(cov.safe_to_format)

    def test_limit_then_catch_up(self):
        res = run_mirror(self.cfg, self.ledger, self.sdcard, limit=2)
        self.assertEqual((res.verified, res.left), (2, 3))
        self.assertFalse(coverage(self.plan(), self.ledger).safe_to_format)
        res = run_mirror(self.cfg, self.ledger, self.sdcard)
        self.assertEqual((res.verified, res.left), (3, 0))
        self.assertTrue(coverage(self.plan(), self.ledger).safe_to_format)

    def test_interrupted_mirror_continues(self):
        real, calls = mirror._mirror_one, []

        def flaky(*a, **kw):
            calls.append(1)
            if len(calls) == 3:
                raise OSError(5, "Input/output error")
            return real(*a, **kw)

        with mock.patch.object(mirror, "_mirror_one", flaky):
            res = run_mirror(self.cfg, self.ledger, self.sdcard)
        self.assertTrue(res.interrupted)
        self.assertEqual((res.verified, res.left), (2, 3))
        res = run_mirror(self.cfg, self.ledger, self.sdcard)
        self.assertEqual((res.verified, res.left, res.interrupted), (3, 0, False))

    def test_a_card_that_gives_back_wrong_data_is_caught(self):
        real, calls = mirror._read_back, []

        def lying(path, on_bytes, weight, result):
            calls.append(1)
            got = real(path, on_bytes, weight, result)
            return "0" * 64 if len(calls) == 2 else got

        with mock.patch.object(mirror, "_read_back", lying):
            res = run_mirror(self.cfg, self.ledger, self.sdcard)
        self.assertEqual((res.verified, res.failed), (4, 1))
        self.assertIn("different data", res.failures[0][1])
        self.assertFalse((self.on_card() / res.failures[0][0]).exists())
        self.assertFalse(coverage(self.plan(), self.ledger).safe_to_format)

    def test_a_damaged_copy_on_the_tablet_is_not_passed_on(self):
        victim = Path(self.result.shoot_folder) / self.shoot_files(self.result)[0]
        data = bytearray(victim.read_bytes())
        data[5000] ^= 0xFF
        victim.write_bytes(bytes(data))
        res = run_mirror(self.cfg, self.ledger, self.sdcard)
        self.assertEqual((res.verified, res.failed), (4, 1))
        self.assertIn("tablet", res.failures[0][1])
        self.assertFalse((self.on_card() / victim.name).exists())

    def test_a_file_removed_from_the_card_is_copied_again(self):
        run_mirror(self.cfg, self.ledger, self.sdcard)
        gone = self.on_card() / "DSC_4822.NEF"
        gone.unlink()
        res = run_mirror(self.cfg, self.ledger, self.sdcard)
        self.assertEqual((res.dropped, res.verified), (1, 1))
        self.assertTrue(gone.exists())

    def test_checking_the_card_finds_a_changed_file(self):
        run_mirror(self.cfg, self.ledger, self.sdcard)
        victim = self.on_card() / "DSC_4823.NEF"
        data = bytearray(victim.read_bytes())
        data[100] ^= 0xFF
        victim.write_bytes(bytes(data))
        shoot = self.ledger.list_shoots()[0]
        ok, problems, absent = verify_card(self.ledger, self.sdcard, shoot)
        self.assertEqual((ok, absent), (4, 0))
        self.assertEqual(problems[0][0], "CHANGED")
        self.assertFalse(coverage(self.plan(), self.ledger).safe_to_format)
        res = run_mirror(self.cfg, self.ledger, self.sdcard)
        self.assertEqual(res.verified, 1)
        self.assertEqual(verify_card(self.ledger, self.sdcard, shoot)[0], 5)

    def test_a_full_card_stops_without_erasing_anything(self):
        usage = mock.Mock(total=10**9, used=10**9, free=1000)
        with mock.patch.object(mirror.shutil, "disk_usage", return_value=usage):
            res = run_mirror(self.cfg, self.ledger, self.sdcard)
        self.assertTrue(res.interrupted)
        self.assertEqual((res.reason, res.verified, res.left), (mirror.FULL, 0, 5))
        self.assertTrue((self.sd / "holiday.jpg").exists())


class CommandTests(Base):
    def run_cli(self, *args):
        env = dict(os.environ, DOUBLETAKE_LIBRARY=str(self.library), DOUBLETAKE_CARD=str(self.tmp / "SD"),
                   XDG_CONFIG_HOME=str(self.tmp / "config"), PYTHONPATH=str(HERE.parent))
        return subprocess.run([sys.executable, "-m", "doubletake", *args], env=env, text=True,
                              capture_output=True, stdin=subprocess.DEVNULL)

    def test_backup_command_makes_both_copies_and_says_when_it_is_safe(self):
        make_card(self.card, count=4)
        source = f"dir:{self.card}"
        out = self.run_cli("backup", "--name", "first", "--source", source)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("Second copy: the folder named in the settings is not there", out.stdout)
        self.assertIn("exist only on the tablet", out.stdout)
        self.assertIn("DO NOT FORMAT THE CAMERA'S CARDS YET: 4 photos have no second copy yet.", out.stdout)

        (self.tmp / "SD").mkdir()
        out = self.run_cli("backup", "--source", source)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("New folder found", out.stdout)
        self.assertIn("Nothing to copy from the camera", out.stdout)
        self.assertIn("SECOND COPY COMPLETE", out.stdout)
        self.assertIn("SAFE TO FORMAT THE CAMERA'S CARDS", out.stdout)

        make_card(self.card, count=2, start=7000)
        out = self.run_cli("backup", "--name", "second", "--source", source, "--limit", "1")
        self.assertIn("BACKUP COMPLETE", out.stdout)
        self.assertIn("DO NOT FORMAT THE CAMERA'S CARDS YET: 1 photo is not on the tablet yet.", out.stdout)

        out = self.run_cli("verify", "--card")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("4 of 4 files in the second copy match", out.stdout)
        out = self.run_cli("card")
        self.assertIn("On it:     5 photos", out.stdout)
        out = self.run_cli("shoots")
        self.assertIn("2nd copy", out.stdout)


if __name__ == "__main__":
    unittest.main()
