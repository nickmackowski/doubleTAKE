"""Tests for the photo viewer's pictures, Mark as Imported, clearing and settings."""

from __future__ import annotations

import io
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from fakes import make_card, make_photo_card  # noqa: E402
from test_engine import Base  # noqa: E402
from test_screens import StationBase  # noqa: E402

from doubletake import cards, config, photos, retention, settings  # noqa: E402
from doubletake.ingest import coverage, make_plan  # noqa: E402
from doubletake.mirror import run_mirror  # noqa: E402
from doubletake.sources.folder import FolderSource  # noqa: E402

try:
    from PIL import Image

    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


@unittest.skipUnless(HAVE_PIL, "the image library is not installed")
class PhotoTests(Base):
    def setUp(self):
        super().setUp()
        self.files = make_photo_card(self.card, count=2, portrait=(1,))

    def test_details_from_the_raw_file(self):
        info = photos.read_details(self.files[0])
        self.assertEqual(info["shutter"], "1/2000 s")
        self.assertEqual(info["aperture"], "f/5.6")
        self.assertEqual(info["iso"], "ISO 400")
        self.assertEqual(info["focal"], "500 mm")
        self.assertEqual(info["taken"], "2026:10:04 14:07:33")
        self.assertEqual(info["model"], "NIKON D850")
        self.assertEqual(info["orientation"], 1)

    def test_thumbnail_and_screen_pictures_are_made_once(self):
        cache = self.tmp / "cache"
        thumb = photos.picture(self.files[0], "thumb", cache)
        self.assertEqual(Image.open(thumb).size, (360, 240))
        stamp = thumb.stat().st_mtime_ns
        self.assertEqual(photos.picture(self.files[0], "thumb", cache).stat().st_mtime_ns, stamp)
        screen = photos.picture(self.files[0], "screen", cache)
        self.assertEqual(Image.open(screen).size, (1200, 800))  # never enlarged
        photos.forget(cache)
        self.assertFalse(cache.exists())

    def test_portrait_photos_are_turned_upright(self):
        thumb = photos.picture(self.files[1], "thumb", self.tmp / "p")
        self.assertEqual(Image.open(thumb).size, (240, 360))
        self.assertEqual(photos.full_size(self.files[1]), (800, 1200))

    def test_100_percent_crop(self):
        body = photos.crop(self.files[0], 0.5, 0.5, 400, 300)
        self.assertEqual(Image.open(io.BytesIO(body)).size, (400, 300))
        edge = photos.crop(self.files[0], 1.0, 0.0, 5000, 300)  # asks for more than there is
        self.assertEqual(Image.open(io.BytesIO(edge)).size, (1200, 300))

    def test_a_file_without_a_preview(self):
        broken = self.tmp / "x.NEF"
        broken.write_bytes(b"II*\x00" + b"\x00" * 100)
        self.assertIsNone(photos.picture(broken, "thumb", self.tmp / "b"))
        self.assertIsNone(photos.crop(broken, 0.5, 0.5, 100, 100))


class RetentionTests(Base):
    def setUp(self):
        super().setUp()
        os.environ.pop("DOUBLETAKE_CARD", None)
        self.sd = self.tmp / "SD_CARD"
        self.sd.mkdir()
        self.cfg["second_copy"] = str(self.sd)
        make_card(self.card, count=4)
        self.result = self.backup()
        self.sdcard = cards.detect(self.cfg, self.ledger).card
        cards.tag(self.sdcard, self.ledger)
        run_mirror(self.cfg, self.ledger, self.sdcard)
        self.shoot = self.ledger.list_shoots()[0]
        self.folder = Path(self.result.shoot_folder)
        self.on_card = self.sd / "DoubleTake" / self.shoot["folder"]
        (self.folder / "my notes.txt").write_text("not the app's")

    LATER = time.time() + 2 * 86400  # past the day a shoot is kept after copying

    def test_kept_for_a_day_after_copying(self):
        with self.assertRaises(ValueError):
            retention.clear_shoot(self.ledger, self.cfg, self.shoot["id"], self.sdcard)
        self.assertEqual(len(list(self.folder.glob("*.NEF"))), 4)
        retention.clear_shoot(self.ledger, self.cfg, self.shoot["id"], self.sdcard, now=self.LATER)
        self.assertEqual(len(list(self.folder.glob("*.NEF"))), 0)  # no need to mark it imported

    def test_clearing_removes_only_the_apps_files(self):
        done = retention.clear_shoot(self.ledger, self.cfg, self.shoot["id"], self.sdcard, now=self.LATER)
        self.assertEqual((done["removed"], done["card_removed"]), (4, 4))
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["my notes.txt"])
        self.assertFalse(self.on_card.exists())
        self.assertTrue((self.sd / "DoubleTake" / ".doubletake" / "card.json").exists())
        # Its photos are still recognised as backed up, so never copied again if still on the
        # camera, but they are on neither copy now: never called safe, and said why.
        with FolderSource(str(self.card)) as source:
            plan = make_plan(source, self.ledger)
        self.assertEqual((plan.already, len(plan.todo)), (4, 0))
        cov = coverage(plan, self.ledger)
        self.assertFalse(cov.safe_to_format)
        self.assertEqual((cov.cleared, cov.on_tablet, cov.on_second), (4, 0, 0))
        # And nothing waits to go to the card.
        self.assertEqual(self.ledger.copy_totals(self.sdcard.card_id), (0, 0, 0))

    def test_card_that_was_out_is_cleaned_when_it_comes_back(self):
        retention.clear_shoot(self.ledger, self.cfg, self.shoot["id"], card=None, now=self.LATER)
        self.assertEqual(len(list(self.on_card.glob("*.NEF"))), 4)
        self.assertEqual(retention.card_cleanup(self.ledger, self.sdcard), 4)
        self.assertFalse(self.on_card.exists())
        self.assertEqual(retention.card_cleanup(self.ledger, self.sdcard), 0)

    def test_imported_is_not_a_second_copy(self):
        self.cfg["second_copy"] = "off"
        make_card(self.card, count=2, start=7000)
        self.backup(name="later")
        with FolderSource(str(self.card)) as source:
            plan = make_plan(source, self.ledger)
        self.assertFalse(coverage(plan, self.ledger).safe_to_format)
        later = [s for s in self.ledger.list_shoots() if s["name"] == "later"][0]
        self.ledger.set_imported(later["id"], time.time())
        self.assertFalse(coverage(plan, self.ledger).safe_to_format)  # only Copy 1 and Copy 2 count


class SettingsTests(Base):
    def setUp(self):
        super().setUp()
        self.config_home = self.tmp / "config"
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config_home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        auto = mock.patch.object(settings, "AUTOSTART", self.tmp / "autostart" / "doubletake.desktop")
        auto.start()
        self.addCleanup(auto.stop)

    def test_changes_are_checked_and_saved(self):
        self.assertEqual(settings.apply(self.cfg, {"theme": "night", "keep_days": 14, "copy_what": "all"}), "")
        saved = json.loads(config.config_path().read_text())
        self.assertEqual((saved["theme"], saved["keep_days"], saved["copy_what"]), ("night", 14, "all"))
        self.assertEqual(self.cfg["theme"], "night")
        self.assertIn("Cannot set", settings.apply(self.cfg, {"theme": "purple"}))

    def test_the_cockpit_look_keeps_the_classic_theme_for_later(self):
        self.cfg["theme"] = "slate"
        self.assertEqual(settings.screen_theme(self.cfg), ("classic", "slate"))
        self.assertEqual(settings.apply(self.cfg, {"look": "cockpit", "cockpit_theme": "cockpit-light"}), "")
        self.assertEqual(settings.screen_theme(self.cfg), ("cockpit", "cockpit-light"))
        self.assertIn("Cannot set", settings.apply(self.cfg, {"cockpit_theme": "slate"}))
        self.assertIn("Cannot set", settings.apply(self.cfg, {"theme": "cockpit"}))
        settings.apply(self.cfg, {"look": "classic"})
        self.assertEqual(settings.screen_theme(self.cfg), ("classic", "slate"))
        self.assertEqual(settings.public(self.cfg)["cockpit_theme"], "cockpit-light")
        self.assertIn("Cannot set", settings.apply(self.cfg, {"library": "/"}))
        self.assertIn("Cannot set", settings.apply(self.cfg, {"keep_days": 3}))

    def test_quick_names_are_cleaned(self):
        settings.apply(self.cfg, {"quick_names": ["KCLT ramp", "KCLT-ramp", "", "../x", "a" * 80]})
        self.assertEqual(self.cfg["quick_names"], ["KCLT-ramp", "x", "a" * 40])

    def test_second_copy_switch_and_existing_settings_are_kept(self):
        config.save({"chunk_mb": 4})
        settings.apply(self.cfg, {"second_copy": "off"})
        saved = json.loads(config.config_path().read_text())
        self.assertEqual((saved["second_copy"], saved["chunk_mb"]), ("off", 4))
        self.assertEqual(settings.public(self.cfg)["second_copy"], "off")

    def test_start_at_login(self):
        self.assertEqual(settings.apply(self.cfg, {"start_at_login": True}), "")
        self.assertIn("gui", settings.AUTOSTART.read_text())
        self.assertTrue(settings.public(self.cfg)["start_at_login"])
        settings.apply(self.cfg, {"start_at_login": False})
        self.assertFalse(settings.AUTOSTART.exists())


@unittest.skipUnless(HAVE_PIL, "the image library is not installed")
class LibraryServiceTests(StationBase):
    def setUp(self):
        super().setUp()
        for p in self.card.rglob("*.NEF"):
            p.unlink()
        make_photo_card(self.card, count=3)
        self.use_card()

    def test_browse_then_delete(self):
        self.station.start()
        self.wait(lambda s: s["camera"]["state"] == "ready", "the camera to be read")
        self.station.start_backup("KCLT ramp")
        self.wait(lambda s: s["format"].get("safe"), "both copies")
        view = self.station.library_view()
        shoot = view["shoots"][0]
        self.assertEqual((shoot["files"], shoot["mirrored"], shoot["imported"]), (3, 3, ""))
        photos_ = self.station.shoot_photos(shoot["id"])
        self.assertEqual([f["name"] for f in photos_["files"]], ["N81_0020.NEF", "N81_0021.NEF", "N81_0022.NEF"])
        first = photos_["files"][0]["id"]
        path, cache = self.station.photo_file(first)
        self.assertIsNotNone(photos.picture(path, "thumb", cache))
        self.assertEqual(self.station.photo_info(first)["width"], 1200)

        # Kept for a day after copying: not deletable yet.
        self.assertFalse(view["shoots"][0]["deletable"])
        self.assertTrue(view["shoots"][0]["deletable_from"])
        self.assertIn("less than a day", self.station.clear([shoot["id"]])["error"])
        # Pretend the day has gone by.
        self.ledger.db.execute("UPDATE files SET copied=copied-2*86400 WHERE shoot_id=?", (shoot["id"],))
        self.ledger.db.commit()
        self.assertTrue(self.station.library_view()["shoots"][0]["deletable"])
        done = self.station.clear([shoot["id"]])
        self.assertEqual((done["error"], done["removed"]), ("", 3))
        view = self.station.library_view()
        self.assertTrue(view["shoots"][0]["cleared"])
        self.assertEqual(view["totals"]["photos"], 0)
        self.assertIsNone(self.station.photo_file(first))
        self.assertFalse(cache.exists())


if __name__ == "__main__":
    unittest.main()
