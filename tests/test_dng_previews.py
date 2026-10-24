"""DNG files keep their preview in different ways. Every way a camera does it is found."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fakes import jpeg_bytes  # noqa: E402

from doubletake import checks, photos  # noqa: E402


def tiff(ifd_entries, payload: bytes) -> bytes:
    """A little-endian TIFF: one IFD with the given (tag, type, count, value) entries, then
    payload at offset 8 + IFD size. Values that are offsets are relative to the payload."""
    n = len(ifd_entries)
    payload_at = 8 + 2 + n * 12 + 4
    out = bytearray(b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", n))
    for tag, typ, count, value, relative in ifd_entries:
        if relative:
            value += payload_at
        if typ == 3 and count == 1:
            out += struct.pack("<HHIHH", tag, typ, count, value, 0)
        else:
            out += struct.pack("<HHII", tag, typ, count, value)
    out += struct.pack("<I", 0)
    return bytes(out + payload)


class DngPreviewTests(unittest.TestCase):
    def write(self, data):
        tmp = tempfile.NamedTemporaryFile(suffix=".DNG", delete=False)
        tmp.write(data)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_a_preview_kept_as_a_jpeg_strip(self):
        jpeg = jpeg_bytes(320, 240)
        path = self.write(tiff([(0x00FE, 4, 1, 1, False), (0x0103, 3, 1, 7, False), (0x0106, 3, 1, 6, False),
                                (0x0111, 4, 1, 0, True), (0x0117, 4, 1, len(jpeg), False)], jpeg))
        self.assertIsNotNone(checks.find_embedded_jpeg(path))
        self.assertEqual(photos.preview_jpeg(path), jpeg)

    def test_the_raw_data_itself_is_not_taken_for_a_preview(self):
        lossless = b"\xff\xd8" + bytes(5000)  # lossless JPEG, CFA layout
        path = self.write(tiff([(0x0103, 3, 1, 7, False), (0x0106, 3, 1, 32803, False),
                                (0x0111, 4, 1, 0, True), (0x0117, 4, 1, len(lossless), False)], lossless))
        self.assertIsNone(checks.find_embedded_jpeg(path))

    def test_an_uncompressed_rgb_preview_is_made_into_a_picture(self):
        w, h = 64, 48
        rgb = bytes((x * 4) % 256 for x in range(w * h * 3))
        path = self.write(tiff([(0x0100, 4, 1, w, False), (0x0101, 4, 1, h, False), (0x0103, 3, 1, 1, False),
                                (0x0106, 3, 1, 2, False), (0x0115, 3, 1, 3, False),
                                (0x0111, 4, 1, 0, True), (0x0117, 4, 1, len(rgb), False)], rgb))
        self.assertIsNone(checks.find_embedded_jpeg(path))
        self.assertEqual(checks.find_rgb_preview(path)[:2], (w, h))
        data = photos.preview_jpeg(path)
        self.assertTrue(data and data[:2] == b"\xff\xd8")
        cache = Path(tempfile.mkdtemp())
        self.assertTrue(photos.picture(path, "thumb", cache).is_file())

    def test_no_preview_at_all_is_none(self):
        path = self.write(tiff([(0x0103, 3, 1, 1, False), (0x0106, 3, 1, 32803, False)], bytes(100)))
        self.assertIsNone(photos.preview_jpeg(path))


if __name__ == "__main__":
    unittest.main()


class SensorDataTests(unittest.TestCase):
    """A DNG with no preview at all (Insta360 Ace Pro 2): drawn from the sensor data."""

    def mosaic_dng(self, w=64, h=48):
        # GRBG, a red left half and a blue right half, black level 4096, white 65535.
        px = bytearray()
        for y in range(h):
            for x in range(w):
                colour = [1, 0, 2, 1][(y % 2) * 2 + (x % 2)]
                want = 0 if x < w // 2 else 2
                px += struct.pack("<H", 40000 if colour == want else 4096)
        extra = struct.pack("<IIIIIIII", 4096, 1, 4096, 1, 4096, 1, 4096, 1)  # BlackLevel rationals
        entries = [(0x0100, 4, 1, w, False), (0x0101, 4, 1, h, False), (0x0102, 3, 1, 16, False),
                   (0x0103, 3, 1, 1, False), (0x0106, 3, 1, 32803, False), (0x0115, 3, 1, 1, False),
                   (0x0111, 4, 1, 0, True), (0x0117, 4, 1, len(px), False),
                   (0x828D, 3, 2, 2 | (2 << 16), False), (0x828E, 1, 4, 0x01020001, False),
                   (0xC61A, 5, 4, len(px), True), (0xC61D, 4, 1, 65535, False)]
        tmp = tempfile.NamedTemporaryFile(suffix=".DNG", delete=False)
        tmp.write(tiff(entries, bytes(px) + extra))
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_found_and_drawn_at_half_size(self):
        from doubletake import rawview

        path = self.mosaic_dng()
        m = rawview.find_mosaic(path)
        self.assertEqual((m["width"], m["height"], m["cfa"], m["black"]), (64, 48, [1, 0, 2, 1], 4096.0))
        img = rawview.render(path)
        self.assertEqual(img.size, (32, 24))
        r, g, b = img.getpixel((4, 12))
        self.assertGreater(r, b + 50)
        r, g, b = img.getpixel((28, 12))
        self.assertGreater(b, r + 50)

    def test_the_viewer_gets_a_thumbnail(self):
        path = self.mosaic_dng()
        self.assertIsNone(photos.preview_jpeg(path))
        self.assertTrue(photos.picture(path, "thumb", Path(tempfile.mkdtemp())).is_file())


class JpegTwinTests(unittest.TestCase):
    """RAW only, with a camera that always makes a JPEG beside each RAW (Insta360 Ace Pro 2)."""

    def setUp(self):
        import shutil

        from doubletake.ledger import Ledger

        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.card = self.tmp / "card" / "DCIM" / "Camera01"
        self.card.mkdir(parents=True)
        from fakes import fake_nef

        for i in range(3):
            (self.card / f"IMG_{i}.dng").write_bytes(fake_nef(50_000, seed=i))
            (self.card / f"IMG_{i}.jpg").write_bytes(jpeg_bytes(400, 300, seed=i))
        (self.card / "LONE.jpg").write_bytes(jpeg_bytes(200, 150))
        self.library = self.tmp / "library"
        self.library.mkdir()
        self.ledger = Ledger(self.library)
        self.addCleanup(self.ledger.close)
        self.cfg = {"library": str(self.library), "copy_what": "raw", "chunk_mb": 1}

    def backup(self):
        from doubletake.ingest import run_backup
        from doubletake.sources.folder import FolderSource

        with FolderSource(str(self.tmp / "card")) as source:
            return run_backup(source, self.cfg, self.ledger, "")

    def test_the_twins_are_not_kept_but_make_the_pictures(self):
        result = self.backup()
        self.assertEqual(result.verified, 3)
        folder = Path(result.shoot_folder)
        self.assertEqual(sorted(p.name for p in folder.glob("*.*")), ["IMG_0.dng", "IMG_1.dng", "IMG_2.dng", "manifest.json"])
        self.assertFalse((folder / ".incoming").exists())
        for row in self.ledger.shoot_files(1):
            cache = self.ledger.dir / "previews" / str(row["id"])
            self.assertTrue((cache / "thumb.jpg").is_file() and (cache / "screen.jpg").is_file(), row["src_name"])

    def test_a_twin_does_not_keep_the_cards_from_being_safe_but_a_lone_jpeg_does(self):
        from doubletake.ingest import copy_choice, make_plan
        from doubletake.sources.folder import FolderSource

        self.backup()
        with FolderSource(str(self.tmp / "card")) as source:
            plan = make_plan(source, self.ledger, copy=copy_choice(self.cfg))
        self.assertEqual((plan.skipped["count"], plan.skipped["jpeg"], plan.skipped["twins"]), (1, 1, 3))
        (self.card / "LONE.jpg").unlink()
        with FolderSource(str(self.tmp / "card")) as source:
            plan = make_plan(source, self.ledger, copy=copy_choice(self.cfg))
        self.assertEqual((plan.skipped["count"], plan.skipped["twins"]), (0, 3))

    def test_raw_and_jpeg_copies_both_as_before(self):
        self.cfg["copy_what"] = "raw_jpeg"
        result = self.backup()
        self.assertEqual(result.verified, 7)
