"""Tests for naming photos from a pattern, on import and afterwards for a whole shoot."""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from fakes import make_card  # noqa: E402
from test_engine import Base  # noqa: E402

from doubletake import cards, naming  # noqa: E402
from doubletake.ingest import coverage, make_plan  # noqa: E402
from doubletake.mirror import run_mirror, verify_card  # noqa: E402
from doubletake.sources.folder import FolderSource  # noqa: E402

WHEN = time.mktime((2026, 10, 11, 14, 12, 5, 0, 0, -1))


class PatternTests(unittest.TestCase):
    def test_make(self):
        self.assertEqual(naming.make("{date}_{shoot}_{seq}", name="DSC_4841.NEF", taken=WHEN, shoot="Ramp sunset", seq=7),
                         "2026-10-11_Ramp-sunset_0007.NEF")
        self.assertEqual(naming.make("{camera}_{num}_{time}", name="DSC_4841.NEF", taken=WHEN, shoot="x", seq=1,
                                     camera="Nikon D850"), "D850_4841_141205.NEF")
        self.assertEqual(naming.make("{camera}-{orig}", name="DSC_4841.JPG", taken=WHEN, shoot="x", seq=1,
                                     camera="Sony α7R V"), "a7R-V-DSC_4841.JPG")

    def test_check(self):
        self.assertEqual(naming.check("{date}_{shoot}_{seq}"), "")
        self.assertIn("different name", naming.check("{date}_{shoot}"))
        self.assertIn("Unknown", naming.check("{date}_{lens}_{seq}"))
        self.assertIn("slash", naming.check("{shoot}/{seq}"))
        self.assertNotEqual(naming.check(""), "")

    def test_raw_and_jpeg_pairs_share_a_name(self):
        files = [("a", "DSC_0002.NEF", WHEN + 2), ("b", "DSC_0001.JPG", WHEN), ("c", "DSC_0001.NEF", WHEN),
                 ("d", "DSC_0003.NEF", WHEN + 4)]
        names = naming.plan_names(files, "{shoot}_{seq}", "Ramp")
        self.assertEqual(names, {"b": "Ramp_0001.JPG", "c": "Ramp_0001.NEF", "a": "Ramp_0002.NEF", "d": "Ramp_0003.NEF"})

    def test_taken_names_are_avoided(self):
        names = naming.plan_names([("a", "DSC_1.NEF", WHEN)], "{shoot}_{seq}", "Ramp", taken_names={"Ramp_0001.NEF"})
        self.assertEqual(names["a"], "Ramp_0001_2.NEF")


class RenameFilesTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(__import__("tempfile").mkdtemp())
        for name in ("a.NEF", "b.NEF"):
            (self.dir / name).write_text(name)

    def test_names_can_swap(self):
        naming.rename_files(self.dir, [("a.NEF", "b.NEF"), ("b.NEF", "a.NEF")])
        self.assertEqual((self.dir / "a.NEF").read_text(), "b.NEF")
        self.assertEqual((self.dir / "b.NEF").read_text(), "a.NEF")

    def test_a_clash_puts_everything_back(self):
        (self.dir / "taken.NEF").write_text("someone else's")
        with self.assertRaises(OSError):
            naming.rename_files(self.dir, [("a.NEF", "c.NEF"), ("b.NEF", "taken.NEF")])
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["a.NEF", "b.NEF", "taken.NEF"])
        self.assertEqual((self.dir / "taken.NEF").read_text(), "someone else's")


class ImportRenameTests(Base):
    def setUp(self):
        super().setUp()
        make_card(self.card, count=4)
        self.cfg.update(rename_on_import=True, rename_pattern="{date}_{shoot}_{seq}")

    def test_photos_get_new_names_and_are_still_recognised(self):
        taken = min(p.stat().st_mtime for p in self.card.rglob("*.NEF"))
        result = self.backup(name="Ramp")
        names = self.shoot_files(result)
        day = time.strftime("%Y-%m-%d", time.localtime(taken))
        self.assertEqual([n for n in names if n.endswith(".NEF")], [f"{day}_ramp_{i:04d}.NEF" for i in range(1, 5)])
        with FolderSource(str(self.card)) as source:
            plan = make_plan(source, self.ledger)
        self.assertEqual(len(plan.todo), 0)  # nothing is copied again
        self.assertEqual(coverage(plan, self.ledger).on_tablet, len(plan.photos))

    def test_a_second_backup_carries_on_numbering(self):
        self.backup(name="Ramp")
        make_card(self.card, count=2, start=4900, base_time=1_791_700_100)  # later the same day
        result = self.backup(name="Ramp")
        nefs = [n for n in self.shoot_files(result) if n.endswith(".NEF")]
        self.assertEqual(len(nefs), 2)  # its own folder, ramp_02, numbered on from ramp_01
        self.assertTrue(nefs[-1].endswith("_0006.NEF"))

    def test_off_keeps_the_camera_names(self):
        self.cfg["rename_on_import"] = False
        result = self.backup(name="Ramp")
        self.assertIn("DSC_4821.NEF", self.shoot_files(result))


class ShootRenameTests(Base):
    def setUp(self):
        super().setUp()
        self.sd = self.tmp / "SD_CARD"
        self.sd.mkdir()
        self.cfg["second_copy"] = str(self.sd)
        os.environ.pop("DOUBLETAKE_CARD", None)
        make_card(self.card, count=3)
        self.result = self.backup(name="Ramp")
        self.shoot_id = self.ledger.list_shoots()[0]["id"]
        self.sdcard = cards.detect(self.cfg, self.ledger).card
        cards.tag(self.sdcard, self.ledger)
        run_mirror(self.cfg, self.ledger, self.sdcard)
        self.folder = Path(self.result.shoot_folder)
        self.on_card = self.sd / "DoubleTake" / self.folder.relative_to(self.library)
        (self.folder / "notes.txt").write_text("mine")

    def nefs(self, folder):
        return sorted(p.name for p in folder.iterdir() if p.suffix == ".NEF")

    def test_rename_with_the_card_in(self):
        done = naming.rename_shoot(self.ledger, self.library, self.shoot_id, "{shoot}_{seq}", self.sdcard)
        self.assertEqual((done["renamed"], done["card_renamed"]), (3, 3))
        self.assertEqual(self.nefs(self.folder), ["ramp_0001.NEF", "ramp_0002.NEF", "ramp_0003.NEF"])
        self.assertEqual(self.nefs(self.on_card), ["ramp_0001.NEF", "ramp_0002.NEF", "ramp_0003.NEF"])
        self.assertEqual((self.folder / "notes.txt").read_text(), "mine")
        self.assertIn("ramp_0001.NEF", (self.on_card / "manifest.json").read_text())
        # the card still checks out, and nothing needs copying again
        matching, problems, missing = verify_card(self.ledger, self.sdcard, self.ledger.shoot(self.shoot_id))
        self.assertEqual((matching, len(problems), missing), (3, 0, 0))
        self.assertEqual(run_mirror(self.cfg, self.ledger, self.sdcard).wanted, 0)
        with FolderSource(str(self.card)) as source:
            self.assertEqual(len(make_plan(source, self.ledger).todo), 0)

    def test_rename_with_the_card_out_catches_up_later(self):
        done = naming.rename_shoot(self.ledger, self.library, self.shoot_id, "{shoot}_{seq}", None)
        self.assertEqual(done["renamed"], 3)
        self.assertEqual(self.nefs(self.on_card), ["DSC_4821.NEF", "DSC_4822.NEF", "DSC_4823.NEF"])  # not yet
        self.assertEqual(naming.apply_pending(self.ledger, self.sdcard, self.library), 3)
        self.assertEqual(self.nefs(self.on_card), ["ramp_0001.NEF", "ramp_0002.NEF", "ramp_0003.NEF"])
        self.assertEqual(self.ledger.pending_renames(self.sdcard.card_id), [])
        self.assertEqual(run_mirror(self.cfg, self.ledger, self.sdcard).wanted, 0)

    def test_renamed_twice_with_the_card_out(self):
        naming.rename_shoot(self.ledger, self.library, self.shoot_id, "{shoot}_{seq}", None)
        naming.rename_shoot(self.ledger, self.library, self.shoot_id, "{date}_{num}", None)
        naming.apply_pending(self.ledger, self.sdcard, self.library)
        self.assertEqual(self.nefs(self.on_card), self.nefs(self.folder))

    def test_imported_shoots_are_not_renamed(self):
        self.ledger.set_imported(self.shoot_id, time.time())
        done = naming.rename_shoot(self.ledger, self.library, self.shoot_id, "{shoot}_{seq}", self.sdcard)
        self.assertIn("marked as imported", done["error"])
        self.assertEqual(self.nefs(self.folder), ["DSC_4821.NEF", "DSC_4822.NEF", "DSC_4823.NEF"])


if __name__ == "__main__":
    unittest.main()
