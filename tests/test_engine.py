"""Tests for the backup engine. Run with:  python -m unittest discover -s tests -v"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from fakes import fake_nef, make_card  # noqa: E402

from doubletake import checks, ingest  # noqa: E402
from doubletake.ingest import BackupError, make_plan, run_backup, shoot_folder_for, shoot_slug  # noqa: E402
from doubletake.ledger import Ledger  # noqa: E402
from doubletake.sources import NotSupported, SourceError, open_source  # noqa: E402
from doubletake.sources.base import SourceFile  # noqa: E402
from doubletake.sources.folder import FolderSource  # noqa: E402
from doubletake.util import local_date, safe_name  # noqa: E402

try:
    import gphoto2  # noqa: F401

    HAVE_GPHOTO2 = True
except ImportError:
    HAVE_GPHOTO2 = False


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doubletake-test-"))
        self.card = self.tmp / "card"
        self.library = self.tmp / "library"
        self.cfg = {
            "library": str(self.library),
            "group_by_year": True,
            "folder_pattern": "date_name",
            "download_method": "auto",
            "chunk_mb": 1,
        }
        self.library.mkdir()
        self.ledger = Ledger(self.library)

    def tearDown(self):
        self.ledger.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def backup(self, source=None, name="KCLT ramp", **kw):
        source = source or FolderSource(str(self.card))
        with source:
            return run_backup(source, self.cfg, self.ledger, name, **kw)

    def shoot_files(self, result):
        return sorted(p.name for p in Path(result.shoot_folder).iterdir() if p.is_file())


class NamingTests(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(safe_name("KCLT ramp / evening"), "KCLT-ramp-evening")
        self.assertEqual(safe_name("  ../../etc  "), "etc")
        self.assertEqual(safe_name(""), "shoot")

    def test_shoot_folders(self):
        self.assertEqual(shoot_folder_for({}, "2026-10-15", ""), "2026_1015_import_01")
        self.assertEqual(shoot_folder_for({}, "2026-10-15", "Ramp sunset", 2), "2026_1015_ramp_sunset_02")
        for typed, slug in (("  Ramp   Sunset ", "ramp_sunset"), ("KCLT N123AB", "kclt_n123ab"),
                            ('a/b\\c:d*e?f"g<h>i|j', "abcdefghij"), ("___x__y_", "x_y"), ("../..", "import"),
                            ("Ramp_ sunset", "ramp_sunset")):
            self.assertEqual(shoot_slug(typed), slug, typed)


class ChecksTests(unittest.TestCase):
    def test_preview_found_in_fake_nef(self):
        with tempfile.NamedTemporaryFile(suffix=".NEF", delete=False) as fh:
            fh.write(fake_nef(50_000, seed=3))
        try:
            found = checks.find_embedded_jpeg(fh.name)
            self.assertIsNotNone(found)
            offset, length = found
            data = Path(fh.name).read_bytes()
            self.assertEqual(data[offset : offset + 2], b"\xff\xd8")
            self.assertEqual(data[offset + length - 2 : offset + length], b"\xff\xd9")
            self.assertEqual(checks.check_container(fh.name, "x.NEF"), (True, ""))
        finally:
            os.unlink(fh.name)

    def test_garbage_is_rejected(self):
        with tempfile.NamedTemporaryFile(suffix=".NEF", delete=False) as fh:
            fh.write(b"\x00" * 5000)
        try:
            self.assertIsNone(checks.find_embedded_jpeg(fh.name))
            self.assertFalse(checks.check_container(fh.name, "x.NEF")[0])
            self.assertIsNone(checks.check_container(fh.name, "x.xyz")[0])
        finally:
            os.unlink(fh.name)


class BackupTests(Base):
    def test_copies_verifies_and_records(self):
        originals = make_card(self.card, count=5)
        result = self.backup()
        self.assertFalse(result.interrupted)
        self.assertEqual((result.verified, result.failed), (5, 0))
        self.assertEqual(result.previews_ok, 5)
        self.assertEqual(Path(result.shoot_folder).name, today_folder("kclt_ramp", 1))
        for original in originals:
            copy = Path(result.shoot_folder) / original.name
            self.assertEqual(sha(copy), sha(original))
            self.assertEqual(int(copy.stat().st_mtime), int(original.stat().st_mtime))
        self.assertTrue((Path(result.shoot_folder) / "manifest.json").is_file())
        self.assertFalse((Path(result.shoot_folder) / ".incoming").exists())
        rows = self.ledger.shoot_files(1)
        self.assertEqual({r["sha256"] for r in rows}, {sha(o) for o in originals})

    def test_second_run_copies_only_new_photos(self):
        make_card(self.card, count=3)
        self.assertEqual(self.backup().verified, 3)
        again = self.backup()
        self.assertEqual((len(again.plan.todo), again.plan.already), (0, 3))
        make_card(self.card, count=2, start=4900, base_time=1_791_700_500)
        third = self.backup()
        self.assertEqual((third.verified, third.plan.already), (2, 3))
        # Each import is a folder of its own: only the new photos are in the second one.
        self.assertEqual(Path(third.shoot_folder).name, today_folder("kclt_ramp", 2))
        self.assertEqual(len([n for n in self.shoot_files(third) if n.endswith(".NEF")]), 2)

    def test_limit_takes_the_oldest_first(self):
        make_card(self.card, count=6)
        result = self.backup(limit=2)
        self.assertEqual(self.shoot_files(result)[:2], ["DSC_4821.NEF", "DSC_4822.NEF"])
        self.assertEqual(result.verified, 2)

    def test_copy_all_does_not_list_a_photo_twice(self):
        make_card(self.card, count=3)
        self.backup()
        result = self.backup(take_all=True)
        self.assertEqual((result.verified, result.failed), (3, 0))
        self.assertEqual(len(self.ledger.shoot_files(1)), 3)
        self.assertEqual(len([n for n in self.shoot_files(result) if n.endswith(".NEF")]), 3)

    def test_same_photo_on_both_card_slots_is_copied_once(self):
        first = make_card(self.card, count=3, folder="store_1/DCIM/100ND850")
        for path in first:
            twin = self.card / "store_2/DCIM/100ND850" / path.name
            twin.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, twin)
        result = self.backup()
        self.assertEqual((result.verified, result.plan.duplicates), (3, 3))

    def test_both_card_slots_with_times_a_second_apart(self):
        # The D850 writes the two cards one after the other, so the same shot can
        # carry slightly different times on each.
        first = make_card(self.card, count=4, folder="store_1/DCIM/100ND850")
        for i, path in enumerate(first):
            twin = self.card / "store_2/DCIM/100ND850" / path.name
            twin.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, twin)
            later = int(path.stat().st_mtime) + (i % 3)  # 0, 1 or 2 seconds later
            os.utime(twin, (later, later))
        result = self.backup()
        self.assertEqual((result.verified, result.plan.duplicates, len(result.plan.todo)), (4, 4, 4))
        again = self.backup()
        self.assertEqual((len(again.plan.todo), again.plan.already, again.plan.duplicates), (0, 4, 4))
        self.assertEqual(len(self.ledger.shoot_files(1)), 4)

    def test_same_name_different_photo_keeps_both(self):
        make_card(self.card, count=1, start=1, folder="DCIM/100ND850")
        make_card(self.card, count=1, start=1, folder="DCIM/101ND850", size=310_000, base_time=1_791_700_900)
        result = self.backup()
        self.assertEqual(result.verified, 2)
        names = [n for n in self.shoot_files(result) if n.endswith(".NEF")]
        self.assertEqual(names, ["DSC_0001.NEF", "DSC_0001_2.NEF"])
        a, b = (Path(result.shoot_folder) / n for n in names)
        self.assertNotEqual(sha(a), sha(b))

    def test_damaged_file_is_reported_not_passed(self):
        make_card(self.card, count=2)
        bad = self.card / "DCIM/100ND850/DSC_9999.NEF"
        bad.write_bytes(b"\x00" * 40_000)
        result = self.backup()
        self.assertEqual((result.verified, result.failed), (2, 1))
        self.assertEqual(result.failures[0][0], "DSC_9999.NEF")
        self.assertFalse((Path(result.shoot_folder) / "DSC_9999.NEF").exists())
        # It is tried again next time, because it never counted as backed up.
        self.assertEqual(len(self.backup().plan.todo), 1)

    def test_short_file_is_a_size_mismatch(self):
        make_card(self.card, count=1)

        class Lying(FolderSource):
            def list_files(self, progress=None):
                return [SourceFile(f.folder, f.name, f.size + 500, f.mtime) for f in super().list_files()]

        result = self.backup(source=Lying(str(self.card)))
        self.assertEqual((result.verified, result.failed), (0, 1))
        self.assertIn("size mismatch", result.failures[0][1])

    def test_not_enough_space_stops_before_copying(self):
        make_card(self.card, count=2)
        real = shutil.disk_usage

        def tiny(_path):
            return real(self.library)._replace(free=1000)

        ingest.shutil.disk_usage = tiny
        try:
            with self.assertRaises(BackupError):
                self.backup()
        finally:
            ingest.shutil.disk_usage = real
        self.assertEqual(self.ledger.list_shoots(), [])


class Flaky(FolderSource):
    """Drops the connection after handing over a set number of bytes."""

    def __init__(self, root, fail_after):
        super().__init__(root)
        self.fail_after = fail_after
        self.served = 0

    def read_chunk(self, f, offset, size):
        if self.served >= self.fail_after:
            raise SourceError("Lost contact with the camera.")
        data = super().read_chunk(f, offset, size)
        self.served += len(data)
        return data


class ResumeTests(Base):
    def test_interrupted_backup_continues_where_it_stopped(self):
        originals = make_card(self.card, count=3, size=5_000_000)
        total = sum(p.stat().st_size for p in originals)
        # Fail part-way through the second file.
        flaky = Flaky(str(self.card), fail_after=8_500_000)
        first = self.backup(source=flaky)
        self.assertTrue(first.interrupted)
        self.assertEqual(first.verified, 1)
        parts = list((Path(first.shoot_folder) / ".incoming").glob("*.part"))
        self.assertEqual(len(parts), 1)
        self.assertGreater(parts[0].stat().st_size, 0)

        counting = Flaky(str(self.card), fail_after=10**12)
        second = self.backup(source=counting)
        self.assertFalse(second.interrupted)
        self.assertEqual((second.verified, second.plan.already), (2, 1))
        for original in originals:
            self.assertEqual(sha(Path(second.shoot_folder) / original.name), sha(original))
        # The part already on the tablet was kept, so less than the remainder was re-read.
        self.assertLess(counting.served, total - originals[0].stat().st_size)
        self.assertFalse((Path(second.shoot_folder) / ".incoming").exists())

    def test_falls_back_when_the_camera_cannot_read_in_pieces(self):
        originals = make_card(self.card, count=2)

        class WholeOnly(FolderSource):
            supports_chunks = True  # claims it can, then refuses

            def read_chunk(self, f, offset, size):
                raise NotSupported("no")

        result = self.backup(source=WholeOnly(str(self.card)))
        self.assertEqual((result.verified, result.method), (2, "whole"))
        self.assertEqual(sha(Path(result.shoot_folder) / originals[0].name), sha(originals[0]))


@unittest.skipUnless(HAVE_GPHOTO2, "the gphoto2 library is not installed")
class CameraLibraryTests(Base):
    def test_backup_through_the_camera_library(self):
        originals = make_card(self.card, count=4)
        with open_source(f"sim:{self.card}") as source:
            info = source.info()
            self.assertEqual(info.kind, "simulated camera")
            plan = make_plan(source, self.ledger)
            self.assertEqual(len(plan.todo), 4)
            result = run_backup(source, self.cfg, self.ledger, "sim", plan=plan)
        self.assertEqual((result.verified, result.failed, result.method), (4, 0, "whole"))
        for original in originals:
            self.assertEqual(sha(Path(result.shoot_folder) / original.name), sha(original))

    def test_missing_folder_gives_a_plain_message(self):
        with self.assertRaises(SourceError):
            open_source("sim:/no/such/folder").open()


class NeverWritesToTheCameraTests(unittest.TestCase):
    def test_no_source_can_change_a_camera(self):
        from doubletake.sources import base, camera
        self.assertFalse(hasattr(base.Source, "set_clock"))
        text = Path(camera.__file__).read_text()
        for call in ("set_config", "set_single_config", "file_delete", "folder_delete", "put_file"):
            self.assertNotIn(call, text)


class CameraNameTests(unittest.TestCase):
    """The camera is shown with its brand, however the camera reports it."""

    def test_names(self):
        from doubletake.sources.camera import camera_name
        cases = [
            (("Nikon Corporation", "D850", "Nikon DSC D850"), "Nikon D850"),
            (("Nikon Corporation", "Z 9", "Nikon Z9"), "Nikon Z 9"),
            (("Canon Inc.", "Canon EOS-1D Mark II", ""), "Canon EOS-1D Mark II"),
            (("FUJIFILM", "X-T5", ""), "Fujifilm X-T5"),
            (("SONY", "SONY ILCE-1", ""), "Sony α1"),
            (("Sony Corporation", "ILCE-7RM5", "Sony Alpha-A7r V"), "Sony α7R V"),
            (("Sony Corporation", "ILCE-7M4", ""), "Sony α7 IV"),
            (("Sony Corporation", "ILCE-7SM3", ""), "Sony α7S III"),
            (("Sony Corporation", "ILCE-7CM2", ""), "Sony α7C II"),
            (("Sony Corporation", "ILCE-7RM4A", ""), "Sony α7R IVA"),
            (("Sony Corporation", "ILCE-9M3", ""), "Sony α9 III"),
            (("Sony Corporation", "ILCE-1M2", ""), "Sony α1 II"),
            (("Sony Corporation", "ILCE-6700", ""), "Sony α6700"),
            (("Sony Corporation", "ILME-FX3", ""), "Sony FX3"),
            (("Sony Corporation", "ZV-E10M2", ""), "Sony ZV-E10 II"),
            (("Sony Corporation", "DSC-RX100M7", ""), "Sony RX100 VII"),
            (("", "ILCE-7RM5", ""), "Sony α7R V"),
            (("Sony Corporation", "ILCE-NEW99", ""), "Sony ILCE-NEW99"),
            (("OM Digital Solutions", "OM-1", ""), "OM System OM-1"),
            (("", "D850", "Nikon DSC D850"), "Nikon D850"),
            (("", "", "Nikon DSC D850"), "Nikon D850"),
            (("Acme Imaging Corp.", "X1", ""), "Acme X1"),
            (("", "", ""), "Camera"),
        ]
        for args, want in cases:
            self.assertEqual(camera_name(*args), want, args)


if __name__ == "__main__":
    unittest.main()


class ShootNameTests(Base):
    def test_a_backup_without_a_name_is_an_import(self):
        make_card(self.card, count=3)
        result = self.backup(name="")
        self.assertEqual(Path(result.shoot_folder).name, today_folder("import", 1))
        self.assertEqual(result.name, "import")

    def test_each_import_on_a_day_gets_the_next_number(self):
        make_card(self.card, count=2, base_time=1_791_700_000)
        one = self.backup(name="")
        make_card(self.card, count=2, start=9000, base_time=1_791_700_000 + 3 * 3600)
        two = self.backup(name="")
        make_card(self.card, count=1, start=9500, base_time=1_791_700_000 + 4 * 3600)
        other = self.backup(name="Ramp Sunset")
        self.assertEqual([Path(r.shoot_folder).name for r in (one, two, other)],
                         [today_folder("import", 1), today_folder("import", 2), today_folder("ramp_sunset", 1)])
        self.assertEqual((one.verified, two.verified, other.verified), (2, 2, 1))

    def test_a_deleted_shoots_number_is_not_used_again(self):
        make_card(self.card, count=2)
        one = self.backup(name="")
        shutil.rmtree(one.shoot_folder)  # gone from the tablet, still in the records
        make_card(self.card, count=1, start=9000, base_time=1_791_700_900)
        two = self.backup(name="")
        self.assertEqual(Path(two.shoot_folder).name, today_folder("import", 2))

    def test_an_import_stopped_part_way_carries_on_in_its_folder(self):
        make_card(self.card, count=3, size=5_000_000)
        first = self.backup(source=Flaky(str(self.card), fail_after=8_500_000), name="")
        self.assertTrue(first.interrupted)
        second = self.backup(name="")
        self.assertEqual(second.shoot_folder, first.shoot_folder)
        self.assertFalse((Path(second.shoot_folder) / ".incoming").exists())
        make_card(self.card, count=1, start=9000, base_time=1_791_700_900)
        self.assertEqual(Path(self.backup(name="").shoot_folder).name, today_folder("import", 2))


def today_folder(slug, n):
    return shoot_folder_for({}, local_date(time.time()), slug, n)
