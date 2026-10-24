"""Tests for What to copy, cameras that plug in as a drive, and opening drives in the kiosk."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from doubletake import cards, ingest  # noqa: E402
from doubletake.sources import drive  # noqa: E402
from doubletake.sources.base import SourceFile, SourceInfo  # noqa: E402


def f(name, size=100, folder="/DCIM/100ND850", mtime=1000):
    return SourceFile(folder, name, size, mtime)


class ChooseTests(unittest.TestCase):
    FILES = [f("DSC_0001.NEF"), f("DSC_0001.JPG"), f("DSC_0001.WAV"), f("DSC_0002.JPG"), f("DSC_0003.MOV"),
             f("DSC_0003.THM"), f("VID_0004.LRV"), f("IMG_0005.dng")]

    def names(self, files):
        return sorted(x.name for x in files)

    def test_raw_only(self):
        keep, skip = ingest.choose(self.FILES, "raw")
        self.assertEqual(self.names(keep), ["DSC_0001.NEF", "DSC_0001.WAV", "IMG_0005.dng"])  # the voice memo goes along
        summary = ingest.skipped_summary(skip)
        self.assertEqual((summary["jpeg"], summary["video"], summary["other"], summary["count"]), (2, 2, 1, 5))

    def test_raw_and_jpeg(self):
        keep, skip = ingest.choose(self.FILES, "raw_jpeg")
        self.assertEqual(self.names(keep), ["DSC_0001.JPG", "DSC_0001.NEF", "DSC_0001.WAV", "DSC_0002.JPG", "IMG_0005.dng"])
        self.assertEqual(self.names(skip), ["DSC_0003.MOV", "DSC_0003.THM", "VID_0004.LRV"])

    def test_everything(self):
        keep, skip = ingest.choose(self.FILES, "all")
        self.assertEqual((len(keep), skip), (len(self.FILES), []))

    def test_the_same_file_on_both_cards_counts_once(self):
        both = [f("DSC_0002.JPG", folder="/store_00010001/DCIM/100ND850"),
                f("DSC_0002.JPG", folder="/store_00020001/DCIM/100ND850")]
        self.assertEqual(ingest.skipped_summary(ingest.choose(both, "raw")[1])["count"], 1)


class FakeSource:
    def __init__(self, files):
        self.files = files

    def info(self):
        return SourceInfo(kind="camera", model="Test", serial="T1")

    def list_files(self, progress=None):
        return list(self.files)


class FakeLedger:
    def __init__(self, safe=True):
        self.safe = safe

    def seen(self, *a):
        return False

    def copy_state(self, *a):
        return (self.safe, self.safe, False)


class CoverageTests(unittest.TestCase):
    def test_never_safe_to_format_while_files_are_left_on_the_camera(self):
        plan = ingest.make_plan(FakeSource([f("A.NEF"), f("B.JPG"), f("C.MOV")]), FakeLedger(), copy="raw")
        self.assertEqual(len(plan.todo), 1)
        cov = ingest.coverage(plan, FakeLedger())
        self.assertTrue(cov.copied_safe)
        self.assertFalse(cov.safe_to_format)

    def test_the_cameras_own_bookkeeping_is_never_copied_or_counted(self):
        listed = [f("A.NEF"), f("fileinfo_list.list"), f(".DS_Store"), f("._A.NEF"), f("CANONMSC.CTG"), f("A.WAV")]
        for choice in ("raw", "raw_jpeg", "all"):
            plan = ingest.make_plan(FakeSource(listed), FakeLedger(), copy=choice)
            self.assertEqual(sorted(x.name for x in plan.todo), ["A.NEF", "A.WAV"], choice)
            self.assertEqual(plan.skipped["count"], 0, choice)
            self.assertTrue(ingest.coverage(plan, FakeLedger()).safe_to_format, choice)

    def test_with_raw_only_the_jpeg_beside_a_safe_raw_is_not_needed(self):
        plan = ingest.make_plan(FakeSource([f("A.NEF"), f("A.JPG")]), FakeLedger(), copy="raw")
        self.assertEqual((len(plan.todo), plan.skipped["count"], plan.skipped["twins"]), (1, 0, 1))
        self.assertTrue(ingest.coverage(plan, FakeLedger()).safe_to_format)
        self.assertFalse(ingest.coverage(plan, FakeLedger(safe=False)).safe_to_format)
        plan = ingest.make_plan(FakeSource([f("A.NEF"), f("A.JPG")]), FakeLedger(), copy="raw_jpeg")
        self.assertTrue(ingest.coverage(plan, FakeLedger()).safe_to_format)

    def test_only_jpegs_with_raw_only(self):
        plan = ingest.make_plan(FakeSource([f("A.JPG"), f("B.JPG")]), FakeLedger(), copy="raw")
        self.assertEqual((plan.found, len(plan.todo), plan.skipped["jpeg"]), (2, 0, 2))
        self.assertFalse(ingest.coverage(plan, FakeLedger()).safe_to_format)

    def test_the_setting(self):
        self.assertEqual(ingest.copy_choice({}), "raw_jpeg")
        self.assertEqual(ingest.copy_choice({"copy_what": "raw"}), "raw")
        self.assertEqual(ingest.copy_choice({"copy_what": "videos"}), "raw_jpeg")


class DriveCameraTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doubletake-drive-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # A stand-in /sys: sdb is a SCSI disk under a USB device that names itself.
        usb = self.tmp / "devices" / "usb1" / "1-2"
        disk = usb / "1-2:1.0" / "host0" / "target0" / "0:0:0:0"
        disk.mkdir(parents=True)
        (usb / "idVendor").write_text("2e1a\n")
        (usb / "manufacturer").write_text("Insta360\n")
        (usb / "product").write_text("Insta360 Ace Pro 2\n")
        (usb / "serial").write_text("IAQE1234\n")
        (disk / "vendor").write_text("Insta360\n")
        (disk / "model").write_text("Ace Pro 2\n")
        block = self.tmp / "class" / "block" / "sdb"
        block.mkdir(parents=True)
        (block / "device").symlink_to(disk)
        self.sys_class = str(self.tmp / "class" / "block")
        self.mount = self.tmp / "run" / "media" / "nick" / "Insta360"
        (self.mount / "DCIM" / "Camera01").mkdir(parents=True)
        (self.mount / "DCIM" / "Camera01" / "IMG_20261013_090102_00_001.dng").write_bytes(b"x" * 10)
        (self.mount / "DCIM" / "Camera01" / "IMG_20261013_090102_00_001.jpg").write_bytes(b"y" * 5)
        (self.mount / "DCIM" / "Camera01" / "VID_20261013_090200_00_002.mp4").write_bytes(b"z" * 50)

    def mountinfo(self):
        return f"1 0 8:17 / {self.mount} rw - exfat /dev/sdb1 rw\n"

    def test_found_and_named(self):
        with mock.patch.object(drive, "MEDIA_ROOTS", (str(self.tmp / "run" / "media") + "/",)):
            cam = drive.find(self.mountinfo(), self.sys_class)
        self.assertIsNotNone(cam)
        self.assertEqual((cam.model, cam.serial), ("Insta360 Ace Pro 2", "drive:IAQE1234"))
        source = drive.DriveCameraSource(cam)
        self.assertEqual(source.info().kind, "camera")
        names = sorted(x.name for x in source.list_files())
        self.assertEqual(len(names), 3)
        keep, skip = ingest.choose(source.list_files(), "raw")
        self.assertEqual([x.name for x in keep], ["IMG_20261013_090102_00_001.dng"])

    def test_an_ordinary_usb_stick_is_not_a_camera(self):
        for name in ("manufacturer", "product"):
            (self.tmp / "devices" / "usb1" / "1-2" / name).write_text("SanDisk\n")
        (self.tmp / "devices" / "usb1" / "1-2" / "1-2:1.0" / "host0" / "target0" / "0:0:0:0" / "vendor").write_text("SanDisk\n")
        self.assertFalse(drive.is_camera(self.mount.with_name("STICK"), "/dev/sdb1", "STICK", self.sys_class))

    def make_reader_card(self, label="NIKON D850", own=False):
        for name in ("manufacturer", "product"):
            (self.tmp / "devices" / "usb1" / "1-2" / name).write_text("Generic USB3.0 Card Reader\n")
        disk = self.tmp / "devices" / "usb1" / "1-2" / "1-2:1.0" / "host0" / "target0" / "0:0:0:0"
        (disk / "vendor").write_text("Generic\n")
        (disk / "model").write_text("STORAGE DEVICE\n")
        mount = self.tmp / "run" / "media" / "nick" / label
        (mount / "DCIM" / "100NCD850").mkdir(parents=True)
        (mount / "DCIM" / "100NCD850" / "DSC_0001.NEF").write_bytes(b"n" * 20)
        if own:
            (mount / ".fieldstation").mkdir()
            (mount / ".fieldstation" / "card.json").write_text("{}")
        uuid = self.tmp / "by-uuid"
        uuid.mkdir(exist_ok=True)
        if not (uuid / "1A2B-3C4D").is_symlink():
            (uuid / "1A2B-3C4D").symlink_to("/dev/sdb1")
        return mount, "/dev/sdb1"

    def test_a_cameras_card_in_a_reader_is_a_camera_card(self):
        mount, dev = self.make_reader_card()
        info = f"1 0 8:17 / {str(mount).replace(' ', '\\040')} rw - exfat {dev} rw\n"
        with mock.patch.object(drive, "MEDIA_ROOTS", (str(self.tmp / "run" / "media") + "/",)), \
             mock.patch.object(drive, "is_maker_camera", lambda *a, **k: False):
            cam = drive.find(info, self.sys_class, str(self.tmp / "by-uuid"))
        self.assertIsNotNone(cam)
        self.assertTrue(cam.reader)
        self.assertEqual((cam.model, cam.serial), ("Camera card (NIKON D850)", "card:1A2B-3C4D"))
        self.assertEqual(drive.DriveCameraSource(cam).info().kind, "card")

    def test_any_card_with_dcim_is_never_the_second_copy(self):
        mount, dev = self.make_reader_card(label="Untitled")
        self.assertTrue(drive.is_camera(mount, "/dev/sdb1"))
        self.assertTrue(drive.is_camera(mount, "/dev/mmcblk0p1"))  # in the tablet's own slot too
        info = f"1 0 8:17 / {mount} rw - exfat /dev/sdb1 rw\n"
        found = cards.candidates("FieldPhotos", None, info, roots=(str(self.tmp / "run" / "media") + "/",))
        self.assertEqual(found, [])

    def test_doubletakes_own_card_is_not_a_camera_card(self):
        mount, dev = self.make_reader_card(label="DT-COPY2", own=True)
        self.assertFalse(drive.is_camera(mount, "/dev/sdb1"))

    def test_never_offered_as_the_second_copy_or_for_exports(self):
        with mock.patch.object(cards, "drive_camera", lambda mount, device: True):
            found = cards.candidates("FieldPhotos", None, self.mountinfo(), roots=(str(self.tmp / "run" / "media") + "/",))
        self.assertEqual(found, [])


class OpenRemovableTests(unittest.TestCase):
    LSBLK = ('NAME="/dev/nvme0n1p3" TYPE="part" FSTYPE="xfs" MOUNTPOINT="/" RM="0" HOTPLUG="0"\n'
             'NAME="/dev/mmcblk0p1" TYPE="part" FSTYPE="exfat" MOUNTPOINT="" RM="1" HOTPLUG="1"\n'
             'NAME="/dev/sdb1" TYPE="part" FSTYPE="exfat" MOUNTPOINT="/run/media/n/X" RM="1" HOTPLUG="1"\n'
             'NAME="/dev/sdc" TYPE="disk" FSTYPE="" MOUNTPOINT="" RM="1" HOTPLUG="1"\n'
             'NAME="/dev/sdc1" TYPE="part" FSTYPE="crypto_LUKS" MOUNTPOINT="" RM="1" HOTPLUG="1"\n'
             'NAME="/dev/sdd1" TYPE="part" FSTYPE="vfat" MOUNTPOINT="" RM="0" HOTPLUG="1"\n')

    def test_only_unopened_cards_and_drives(self):
        self.assertEqual(cards.removable_to_open(self.LSBLK), ["/dev/mmcblk0p1", "/dev/sdd1"])

    def test_opened_through_the_system(self):
        ran = []

        def run(args):
            ran.append(args)
            return SimpleNamespace(stdout=self.LSBLK if args[0] == "lsblk" else "", returncode=0)

        self.assertEqual(cards.open_removable(run), ["/dev/mmcblk0p1", "/dev/sdd1"])
        self.assertIn(["udisksctl", "mount", "--no-user-interaction", "-b", "/dev/mmcblk0p1"], ran)


class CardOrCameraTests(unittest.TestCase):
    """A card read in a reader and the same card read through the camera are the same photos."""

    def test_the_same_photo_by_either_way(self):
        from doubletake.ledger import Ledger
        from doubletake.sources.base import SourceFile

        tmp = Path(tempfile.mkdtemp(prefix="doubletake-ledger-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        led = Ledger(tmp)
        shoot = led.get_or_create_shoot("2026/x", "x", "2026-10-15", "Nikon D850", "3001234")
        photo = SourceFile("/DCIM/100NCD850", "DSC_0001.NEF", 45_000_000, 1792000000)
        led.add_file(shoot, "3001234", photo, "ab" * 32, "DSC_0001.NEF", "verified")
        self.assertTrue(led.seen("card:1A2B-3C4D", photo.name, photo.size, photo.mtime))  # in a reader
        self.assertFalse(led.seen("card:1A2B-3C4D", photo.name, photo.size + 1, photo.mtime))
        self.assertFalse(led.seen("other-camera", photo.name, photo.size, photo.mtime))
        shoot2 = led.get_or_create_shoot("2026/y", "y", "2026-10-15", "Camera card", "card:1A2B-3C4D")
        later = SourceFile("/DCIM/100NCD850", "DSC_0002.NEF", 46_000_000, 1792000100)
        led.add_file(shoot2, "card:1A2B-3C4D", later, "cd" * 32, "DSC_0002.NEF", "verified")
        self.assertTrue(led.seen("3001234", later.name, later.size, later.mtime))  # then through the camera
        self.assertEqual(led.copy_state("3001234", later.name, later.size, later.mtime), (True, False, False))
        led.close()


if __name__ == "__main__":
    unittest.main()


class UsbStyleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doubletake-style-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def disk(self, removable, size_gb, driver):
        iface = self.tmp / "devices" / "usb1" / "1-2" / "1-2:1.0"
        scsi = iface / "host0" / "target0" / "0:0:0:0"
        scsi.mkdir(parents=True)
        drivers = self.tmp / "drivers" / driver
        drivers.mkdir(parents=True)
        (iface / "driver").symlink_to(drivers)
        block = self.tmp / "block" / "sdb"
        block.mkdir(parents=True)
        (block / "removable").write_text(f"{removable}\n")
        (block / "size").write_text(f"{int(size_gb * 1e9 / 512)}\n")
        (block / "device").symlink_to(scsi)
        return str(self.tmp / "block")

    def test_a_thumb_drive(self):
        self.assertEqual(cards.usb_style("/dev/sdb1", self.disk(1, 64, "usb-storage")), "stick")

    def test_an_external_ssd_on_uas(self):
        self.assertEqual(cards.usb_style("/dev/sdb1", self.disk(1, 500, "uas")), "drive")

    def test_not_removable_is_a_drive(self):
        self.assertEqual(cards.usb_style("/dev/sdb1", self.disk(0, 256, "usb-storage")), "drive")

    def test_big_is_a_drive(self):
        self.assertEqual(cards.usb_style("/dev/sdb1", self.disk(1, 2000, "usb-storage")), "drive")

    def test_the_tablets_own_slot_is_a_card(self):
        self.assertEqual(cards.Card(Path("/run/media/x"), device="/dev/mmcblk0p1").style, "card")


class OnePerDiskTests(unittest.TestCase):
    def test_a_mac_formatted_drive_is_one_drive(self):
        efi = cards.Card(Path("/run/media/u/EFI"), device="/dev/sdb1", total=200_000_000)
        main = cards.Card(Path("/run/media/u/Photos"), device="/dev/sdb2", total=2_000_000_000_000)
        other = cards.Card(Path("/run/media/u/STICK"), device="/dev/sdc1", total=64_000_000_000)
        found = cards.one_per_disk([efi, main, other])
        self.assertEqual([c.device for c in found], ["/dev/sdb2", "/dev/sdc1"])

    def test_disk_names(self):
        self.assertEqual(cards.disk_of("/dev/mmcblk0p1"), "/dev/mmcblk0")
        self.assertEqual(cards.disk_of("/dev/sdb12"), "/dev/sdb")
