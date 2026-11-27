import io
import tempfile
import unittest
import urllib.request
from pathlib import Path

from PIL import Image

from doubletake import phone, settings, watermark


def photo(path: Path, size=(1200, 800), color=(40, 60, 80)) -> Path:
    Image.new("RGB", size, color).save(path, "JPEG", quality=95)
    return path


class StampTests(unittest.TestCase):
    def test_lines_are_trimmed_and_at_most_two(self):
        self.assertEqual(watermark.clean_lines(["  a   b ", "", "c", "d"]), ["a b"])
        self.assertEqual(watermark.clean_lines(["x" * 80])[0], "x" * 60)
        self.assertEqual(watermark.clean_lines(None), [])

    def test_the_mark_runs_corner_to_corner_through_the_middle(self):
        for size in ((1500, 1000), (1000, 1500)):
            img = Image.new("RGB", size, (40, 60, 80))
            out = watermark.stamp(img, ["© Nick Mackowski", "nickmackowskiphotography.com"])
            self.assertEqual(out.size, size)
            w, h = size
            middle = out.crop((w // 2 - 80, h // 2 - 80, w // 2 + 80, h // 2 + 80)).convert("L")
            self.assertGreater(middle.getextrema()[1], 180)  # white letters in the middle
            # The top-left and bottom-right corners are off the diagonal: untouched.
            for box in ((0, 0, w // 8, h // 8), (w - w // 8, h - h // 8, w, h)):
                self.assertEqual(out.crop(box).getcolors(), [((w // 8) * (h // 8), (40, 60, 80))])

    def test_no_lines_no_mark(self):
        img = Image.new("RGB", (300, 200), (10, 20, 30))
        self.assertEqual(watermark.stamp(img, ["", " "]).getcolors(), [(300 * 200, (10, 20, 30))])

    def test_a_marked_copy_keeps_the_photo_upright_and_its_size(self):
        root = Path(tempfile.mkdtemp())
        src = root / "a.jpg"
        img = Image.new("RGB", (600, 400), (40, 60, 80))
        exif = img.getexif()
        exif[0x0112] = 6  # turned: seen as 400 x 600
        exif[0x013B] = "Nick"
        img.save(src, "JPEG", exif=exif.tobytes())
        body = watermark.marked_jpeg(src, ["Nick"])
        out = Image.open(io.BytesIO(body))
        self.assertEqual(out.size, (400, 600))
        self.assertNotIn(0x0112, out.getexif())
        self.assertEqual(out.getexif().get(0x013B), "Nick")


class SettingsTests(unittest.TestCase):
    def test_until_set_the_lines_are_your_name_and_website(self):
        cfg = {"owner_name": "Nick Mackowski", "owner_website": "https://example.com/"}
        self.assertEqual(settings.watermark_lines(cfg), ["© Nick Mackowski", "example.com"])
        self.assertEqual(settings.watermark_lines({"watermark_lines": ["A", ""]}), ["A"])

    def test_lines_are_checked(self):
        cfg = {"library": tempfile.mkdtemp()}
        self.assertIn("too long", settings.apply(cfg, {"watermark_lines": ["x" * 61, ""]}))
        self.assertTrue(settings.apply(cfg, {"watermark_lines": "nope"}))
        self.assertTrue(settings.apply(cfg, {"watermark": "yes"}))


class PhoneTests(unittest.TestCase):
    def _get(self, share, rest):
        with urllib.request.urlopen(share.url + rest, timeout=10) as r:
            return r.read()

    def test_the_phone_gets_a_marked_copy_only_when_the_watermark_is_on(self):
        root = Path(tempfile.mkdtemp())
        src = photo(root / "a.jpg")
        original = src.read_bytes()
        lines = []
        share = phone.PhoneShare([("Game", "a.jpg", src)], "127.0.0.1", port=0, mark=lambda: lines,
                                 marked=root / "marked")
        share.port = 0
        share.start()
        share.port = share._server.server_address[1]
        try:
            self.assertEqual(self._get(share, "f/Game/a.jpg"), src.read_bytes())
            before = share.items()[0]["v"]
            lines.append("© Nick")
            marked = self._get(share, "f/Game/a.jpg")
            self.assertNotEqual(marked, src.read_bytes())
            self.assertNotEqual(share.items()[0]["v"], before)  # the phone fetches it again
            self.assertEqual(Image.open(io.BytesIO(marked)).size, (1200, 800))
            self.assertTrue(any((root / "marked").glob("*.jpg")))
            self.assertEqual(Image.open(io.BytesIO(self._get(share, "t/Game/a.jpg"))).size[0], 600)
        finally:
            share.stop()
        self.assertFalse(any((root / "marked").glob("*.jpg")))  # cleared when the share ends
        self.assertEqual(src.read_bytes(), original)  # the export itself is never changed


if __name__ == "__main__":
    unittest.main()
