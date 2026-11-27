"""Rotate: a photo turned 90 degrees at a time in DoubleTake's own pictures, for cameras that
record no orientation (the Insta360, Nick 2026-11-27). The photo file never changes."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from doubletake import editor, photos  # noqa: E402

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:  # pragma: no cover
    HAVE_PIL = False

if HAVE_PIL:
    from fakes import jpeg_bytes, nef_with_preview


class TurnRecordTests(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="doubletake-turn-"))
        self.addCleanup(shutil.rmtree, self.data, True)

    def test_a_crop_box_turns_with_the_photo(self):
        self.assertEqual(editor.turn_box([0.1, 0.2, 0.5, 0.6]), [0.4, 0.1, 0.8, 0.5])
        self.assertEqual(editor.turn_box([0.1, 0.2, 0.5, 0.6], 4), [0.1, 0.2, 0.5, 0.6])
        self.assertIsNone(editor.turn_box(None))

    def test_four_taps_and_it_is_back(self):
        self.assertEqual(editor.turn_of(self.data, 7), 0)
        self.assertEqual([editor.turn_once(self.data, 7) for _ in range(4)], [90, 180, 270, 0])
        self.assertEqual(editor.turns(self.data), {})

    def test_turns_are_kept_apart_from_the_edits(self):
        editor.save(self.data, 3, {"exposure": 0.5, "crop": [0.1, 0.2, 0.5, 0.6]})
        editor.turn_once(self.data, 3)
        editor.turn_once(self.data, 5)
        self.assertEqual(editor.turns(self.data), {3: 90, 5: 90})
        self.assertEqual(editor.load(self.data, 3)["crop"], [0.4, 0.1, 0.8, 0.5])
        self.assertEqual(editor.load(self.data, 3)["exposure"], 0.5)
        self.assertFalse(editor.is_edited(editor.load(self.data, 5)))  # turned only: not "edited"
        editor.save(self.data, 3, editor.neutral())  # Reset in the editor
        self.assertEqual(editor.turn_of(self.data, 3), 90)  # the turn stays
        editor.forget(self.data, 3)  # the photo deleted with its shoot
        self.assertEqual(editor.turns(self.data), {5: 90})

    def test_a_damaged_record_is_no_turn(self):
        (self.data / "turns.json").write_text("{not json")
        self.assertEqual(editor.turns(self.data), {})
        (self.data / "turns.json").write_text('{"4": 45, "x": 90, "6": "90", "8": 270}')
        self.assertEqual(editor.turns(self.data), {8: 270})


@unittest.skipUnless(HAVE_PIL, "needs Pillow")
class TurnPictureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="doubletake-turnpic-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.photo = self.root / "IMG_20260906_151853_037.jpg"
        self.photo.write_bytes(jpeg_bytes(1600, 1000, seed=7))  # landscape, no orientation recorded
        self.before = self.photo.read_bytes()

    def size(self, path):
        with Image.open(path) as img:
            return img.size

    def test_thumbnails_and_screen_pictures_turn(self):
        cache = self.root / "previews" / "1"
        plain = photos.picture(self.photo, "thumb", cache)
        turned = photos.picture(self.photo, "thumb", cache, 90)
        self.assertNotEqual(plain, turned)  # each kept under its own name
        w, h = self.size(plain)
        self.assertEqual(self.size(turned), (h, w))
        self.assertEqual(self.size(photos.picture(self.photo, "thumb", cache, 180)), (w, h))
        self.assertEqual(photos.full_size(self.photo), (1600, 1000))
        self.assertEqual(photos.full_size(self.photo, 270), (1000, 1600))
        self.assertIsNotNone(photos.crop(self.photo, 0.5, 0.5, 400, 400, 1.0, 90))
        photos.forget(cache)
        self.assertFalse(cache.exists())
        self.assertEqual(self.photo.read_bytes(), self.before)  # the photo itself never changes

    def test_the_turn_is_clockwise(self):
        img = Image.new("RGB", (40, 20), "black")
        img.paste((255, 0, 0), (0, 0, 20, 20))  # red on the left
        turned = photos._turn(img, 1, 90)
        self.assertEqual(turned.size, (20, 40))
        self.assertEqual(turned.getpixel((10, 5)), (255, 0, 0))  # left goes to the top

    def test_an_export_comes_out_turned(self):
        nef = self.root / "DSC_0001.NEF"
        nef.write_bytes(nef_with_preview(jpeg_bytes(1600, 1000, seed=5)))
        before = nef.read_bytes()
        target = self.root / "Exports" / "DSC_0001.jpg"
        done = editor.export(nef, {}, target, "1080", "high", turn=90)
        self.assertEqual((done["width"], done["height"]), (675, 1080))
        self.assertEqual(nef.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
