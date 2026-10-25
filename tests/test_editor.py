"""Tests for quick edits, JPEG exports, copying to a USB stick and sending to a phone."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from doubletake import editor, phone, photos, settings, usb  # noqa: E402

try:
    from PIL import Image, ImageStat
    HAVE_PIL = True
except ImportError:  # pragma: no cover
    HAVE_PIL = False

if HAVE_PIL:
    from fakes import jpeg_bytes, nef_with_preview


class CleanTests(unittest.TestCase):
    def test_ranges_and_unknown_keys(self):
        p = editor.clean({"exposure": 9, "contrast": -500, "angle": "x", "bw": 1, "evil": 3, "aspect": "4:5",
                          "crop": [0.1, 0.2, 0.9, 0.8], "sharpen": float("nan")})
        self.assertEqual(p["exposure"], 3.0)
        self.assertEqual(p["contrast"], -100)
        self.assertEqual(p["angle"], 0.0)
        self.assertFalse(p["bw"])  # only True itself counts
        self.assertNotIn("evil", p)
        self.assertEqual(p["aspect"], "4:5")
        self.assertEqual(p["crop"], [0.1, 0.2, 0.9, 0.8])
        self.assertEqual(p["sharpen"], 0)

    def test_whole_picture_is_no_crop(self):
        self.assertIsNone(editor.clean({"crop": [0, 0, 1, 1]})["crop"])
        self.assertIsNone(editor.clean({"crop": [0.5, 0.5, 0.505, 0.9]})["crop"])  # too thin

    def test_save_and_load(self):
        data = tempfile.mkdtemp()
        editor.save(data, 7, {"exposure": 0.5})
        self.assertEqual(editor.load(data, 7)["exposure"], 0.5)
        self.assertEqual(editor.edited_ids(data), {7})
        editor.save(data, 7, editor.neutral())  # back to neutral removes the file
        self.assertEqual(editor.edited_ids(data), set())
        self.assertEqual(editor.load(data, 8), editor.neutral())


@unittest.skipUnless(HAVE_PIL, "needs Pillow")
class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.img = Image.open(io.BytesIO(jpeg_bytes(900, 600, seed=3))).convert("RGB")

    def mean(self, img):
        return ImageStat.Stat(img.convert("L")).mean[0]

    def test_neutral_changes_nothing(self):
        self.assertEqual(editor.apply(self.img, {}).tobytes(), self.img.tobytes())

    def test_exposure_and_white_balance(self):
        base = self.mean(self.img)
        self.assertGreater(self.mean(editor.apply(self.img, {"exposure": 1})), base + 10)
        self.assertLess(self.mean(editor.apply(self.img, {"exposure": -1})), base - 10)
        warm = ImageStat.Stat(editor.apply(self.img, {"temp": 60})).mean
        cool = ImageStat.Stat(editor.apply(self.img, {"temp": -60})).mean
        self.assertGreater(warm[0] - warm[2], cool[0] - cool[2])
        self.assertAlmostEqual(self.mean(editor.apply(self.img, {"temp": 60})), base, delta=4)  # same brightness

    def test_black_and_white(self):
        r, g, b = ImageStat.Stat(editor.apply(self.img, {"bw": True})).mean
        self.assertAlmostEqual(r, g, delta=0.5)
        self.assertAlmostEqual(g, b, delta=0.5)

    def test_straighten_leaves_no_empty_corners(self):
        flat = Image.new("RGB", (900, 600), (200, 100, 50))
        turned = editor.apply(flat, {"angle": 10})
        for xy in ((0, 0), (turned.size[0] - 1, 0), (0, turned.size[1] - 1), (turned.size[0] - 1, turned.size[1] - 1)):
            self.assertGreater(sum(turned.getpixel(xy)), 300, xy)

    def test_crop_and_size(self):
        out = editor.apply(self.img, {"crop": [0.0, 0.0, 0.5, 1.0]}, size=200)
        self.assertEqual(max(out.size), 200)
        self.assertAlmostEqual(out.size[0] / out.size[1], 450 / 600, delta=0.02)


@unittest.skipUnless(HAVE_PIL, "needs Pillow")
class ExportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="doubletake-export-"))
        self.nef = self.root / "lib" / "2026" / "2026-10-11_Ramp" / "DSC_4841.NEF"
        self.nef.parent.mkdir(parents=True)
        self.nef.write_bytes(nef_with_preview(jpeg_bytes(1600, 1000, seed=5)))
        self.library = self.root / "lib"

    def test_export_is_a_jpeg_beside_the_shoots(self):
        target = editor.export_path(self.library, "2026/2026-10-11_Ramp", "DSC_4841.NEF")
        self.assertEqual(target, self.library / "Exports" / "2026-10-11_Ramp" / "DSC_4841.jpg")
        before = self.nef.read_bytes()
        done = editor.export(self.nef, {"exposure": 0.3, "crop": [0.1, 0.1, 0.9, 0.9]}, target, "1080", "standard")
        self.assertEqual(self.nef.read_bytes(), before)  # the RAW file is never changed
        with Image.open(target) as img:
            img.load()
        self.assertEqual(img.format, "JPEG")
        self.assertEqual(max(img.size), 1080)
        self.assertEqual(img.getexif().get(0x0110), "NIKON D850")
        self.assertEqual(img.getexif().get(0x0132), "2026:10:04 14:07:33")
        self.assertEqual(done["width"], img.size[0])
        self.assertEqual([p.name for p in target.parent.iterdir()], ["DSC_4841.jpg"])  # no part files left

    def test_list_and_find_exports(self):
        target = editor.export_path(self.library, "2026/2026-10-11_Ramp", "DSC_4841.NEF")
        editor.export(self.nef, {}, target, "1080", "high")
        listed = editor.list_exports(self.library)
        self.assertEqual([s["shoot"] for s in listed], ["2026-10-11_Ramp"])
        self.assertEqual(listed[0]["files"][0]["name"], "DSC_4841.jpg")
        self.assertEqual(editor.export_file(self.library, "2026-10-11_Ramp", "DSC_4841.jpg"), target.resolve())
        for shoot, name in (("..", "x.jpg"), ("2026-10-11_Ramp", "../../2026/2026-10-11_Ramp/DSC_4841.NEF"),
                            ("2026-10-11_Ramp", "DSC_4841.NEF"), ("", "DSC_4841.jpg")):
            self.assertIsNone(editor.export_file(self.library, shoot, name), (shoot, name))


class SettingsTests(unittest.TestCase):
    def test_sort_and_zoom(self):
        cfg = {"photo_sort": "taken_asc", "viewer_zoom": 100}
        with mock.patch.object(settings.config, "save"):
            self.assertEqual(settings.apply(cfg, {"photo_sort": "name_desc", "viewer_zoom": 50}), "")
            self.assertEqual((cfg["photo_sort"], cfg["viewer_zoom"]), ("name_desc", 50))
            self.assertNotEqual(settings.apply(cfg, {"photo_sort": "size_asc"}), "")
            self.assertNotEqual(settings.apply(cfg, {"viewer_zoom": 60}), "")

    def test_viewer_info_shown_or_hidden(self):
        cfg = {}
        with mock.patch.object(settings.config, "save"):
            self.assertEqual(settings.apply(cfg, {"viewer_info": False}), "")
            self.assertIs(cfg["viewer_info"], False)
            self.assertNotEqual(settings.apply(cfg, {"viewer_info": "no"}), "")
            self.assertIs(cfg["viewer_info"], False)


@unittest.skipUnless(HAVE_PIL, "needs Pillow")
class ZoomTests(unittest.TestCase):
    def test_crop_at_half_size(self):
        root = Path(tempfile.mkdtemp())
        nef = root / "a.NEF"
        nef.write_bytes(nef_with_preview(jpeg_bytes(1600, 1000, seed=1)))
        body = photos.crop(nef, 0.5, 0.5, 800, 600, 0.5)
        self.assertEqual(Image.open(io.BytesIO(body)).size, (400, 300))


class UsbTests(unittest.TestCase):
    def setUp(self):
        self.src = Path(tempfile.mkdtemp(prefix="doubletake-src-"))
        self.stick = Path(tempfile.mkdtemp(prefix="doubletake-stick-"))
        (self.stick / "holiday.txt").write_text("someone else's file")
        self.files = []
        for i in range(3):
            p = self.src / f"DSC_{i}.jpg"
            p.write_bytes(os.urandom(50_000))
            self.files.append(("2026-10-11_Ramp", p.name, str(p)))

    def test_copy_checks_every_file_and_leaves_others(self):
        seen = []
        done = usb.copy(self.files, str(self.stick), lambda i, name: seen.append(name))
        self.assertEqual(done, {"copied": 3, "problems": []})
        self.assertEqual(seen, ["DSC_0.jpg", "DSC_1.jpg", "DSC_2.jpg"])
        for _, name, path in self.files:
            self.assertEqual((self.stick / usb.FOLDER / "2026-10-11_Ramp" / name).read_bytes(), Path(path).read_bytes())
        self.assertEqual((self.stick / "holiday.txt").read_text(), "someone else's file")
        self.assertEqual(sorted(p.name for p in (self.stick / usb.FOLDER / "2026-10-11_Ramp").iterdir()),
                         ["DSC_0.jpg", "DSC_1.jpg", "DSC_2.jpg"])

    def test_a_bad_copy_is_not_kept(self):
        real = usb._sha
        calls = {"n": 0}

        def flaky(path, drop_cache=False):
            calls["n"] += 1
            return "bad" if drop_cache and "DSC_1" in str(path) else real(path, drop_cache)

        with mock.patch.object(usb, "_sha", flaky):
            done = usb.copy(self.files, str(self.stick))
        self.assertEqual(done["copied"], 2)
        self.assertIn("DSC_1.jpg", done["problems"][0])
        self.assertFalse((self.stick / usb.FOLDER / "2026-10-11_Ramp" / "DSC_1.jpg").exists())
        self.assertEqual([p for p in (self.stick / usb.FOLDER / "2026-10-11_Ramp").iterdir() if p.name.startswith(".")], [])

    def test_the_second_copy_card_is_not_offered(self):
        from doubletake import cards
        stick = cards.Card(mount=self.stick, device="/dev/sdb1", label="STICK", total=8_000_000_000, free=7_000_000_000)
        sd = cards.Card(mount=self.src, device="/dev/mmcblk0p1", label="SD_CARD")
        tagged = cards.Card(mount=self.src, device="/dev/sdc1", label="OLD", card_id="abc")
        with mock.patch.object(cards, "candidates", lambda folder, library: [stick, sd, tagged]):
            self.assertEqual([d["label"] for d in usb.drives({"library": "/x"})], ["STICK"])

    def test_disk_of_a_partition(self):
        self.assertEqual(usb._whole_disk("/dev/sdb1"), "/dev/sdb")
        self.assertEqual(usb._whole_disk("/dev/mmcblk0p1"), "")


class PhoneTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="doubletake-phone-"))
        (self.dir / "secret.jpg").write_bytes(b"not shared")
        self.photo = self.dir / "DSC_4841.jpg"
        self.photo.write_bytes(jpeg_bytes(400, 300) if HAVE_PIL else b"\xff\xd8 jpeg")
        self.closed = []
        self.share = phone.PhoneShare([("2026-10-11_Ramp", "DSC_4841.jpg", str(self.photo))], "127.0.0.1", port=0,
                                      on_stop=lambda: self.closed.append(True))
        # port 0: let the system pick one for the test
        self.share.start()
        self.share.port = self.share._server.server_address[1]
        self.addCleanup(self.share.stop)

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.share.port}{path}", timeout=5) as reply:
                return reply.status, reply.read(), dict(reply.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, b"", {}

    def test_page_and_photo_need_the_code(self):
        code, body, _ = self.get(f"/{self.share.token}/")
        self.assertEqual(code, 200)
        self.assertIn(b"DSC_4841.jpg", body)
        self.assertEqual(self.get(f"/{self.share.token}/t/0.jpg")[0], 200)  # the thumbnail the page links to
        code, body, headers = self.get(f"/{self.share.token}/f/0/DSC_4841.jpg?dl=1")
        self.assertEqual((code, body), (200, self.photo.read_bytes()))
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        self.assertEqual(self.share.status()["served"], 1)
        for path in ("/", "/wrongcode/", f"/{self.share.token}/f/1/x.jpg", f"/{self.share.token}/../secret.jpg",
                     f"/{self.share.token}/x/0"):
            self.assertEqual(self.get(path)[0], 404, path)

    def test_stops_when_time_is_up(self):
        self.share.until = time.time() - 1
        self.assertEqual(self.get(f"/{self.share.token}/")[0], 404)
        self.assertFalse(self.share.running())
        self.share.stop()
        self.assertEqual(self.closed, [True])  # the firewall is closed again

    def test_wifi_code(self):
        self.assertEqual(phone.wifi_text("Field;Station", 'pa"ss'), 'WIFI:T:WPA;S:Field\\;Station;P:pa\\"ss;;')


if __name__ == "__main__":
    unittest.main()
