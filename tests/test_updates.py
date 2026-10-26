"""Updates in Maintenance, the version as people read it, and the quiet TRON desktop."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_helper import HelperBase  # noqa: E402

from doubletake import maintenance, updates  # noqa: E402
from doubletake.util import version_text  # noqa: E402


class VersionTests(unittest.TestCase):
    def test_in_the_package_managers_order(self):
        order = ["0.9.9", "1.0~pr1", "1.0~pr2", "1.0", "1.0c", "1.0.1~pr1", "1.0.1", "1.0.1c", "1.1c", "1.10c", "2.0"]
        for older, newer in zip(order, order[1:]):
            self.assertEqual(updates.vercmp(older, newer), -1, (older, newer))
            self.assertEqual(updates.vercmp(newer, older), 1, (newer, older))
        self.assertEqual(updates.vercmp("1.0c", "1.0c"), 0)

    def test_as_people_read_it(self):
        self.assertEqual(version_text("1.0~pr1"), "1.0 pre-release 1")
        self.assertEqual(version_text("1.0c"), "1.0c")


class ReleaseTests(unittest.TestCase):
    ASSETS = {"doubletake-1.0c-1.el9.x86_64.rpm": "u1", "doubletake-1.0c-1.fc.x86_64.rpm": "u2",
              "install.sh": "u3", "SHA256SUMS": "u4", "doubletake-1.0c.tar.gz": "u5"}

    def test_the_package_for_this_system(self):
        self.assertEqual(updates.pick_assets(self.ASSETS, "el9")["rpm"][0], "doubletake-1.0c-1.el9.x86_64.rpm")
        self.assertEqual(updates.pick_assets(self.ASSETS, "fc")["rpm"][0], "doubletake-1.0c-1.fc.x86_64.rpm")
        self.assertEqual(set(updates.pick_assets(self.ASSETS, "fc")), {"rpm", "install", "sums"})

    def test_the_latest_release_from_github(self):
        body = json.dumps({"tag_name": "v1.0c", "assets": [{"name": k, "browser_download_url": v}
                                                          for k, v in self.ASSETS.items()]}).encode()

        class Reply(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        found = updates.latest_release(opener=lambda req, timeout: Reply(body))
        self.assertEqual(found["version"], "1.0c")
        self.assertIn("install.sh", found["assets"])
        failed = updates.latest_release(opener=mock.Mock(side_effect=OSError("offline")))
        self.assertIn("error", failed)

    def test_downloads_are_checked_against_the_checksums(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "a.rpm").write_bytes(b"package")
        (folder / "install.sh").write_bytes(b"script")
        good = "\n".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in folder.iterdir())
        self.assertEqual(updates.check_sums(folder, good, ["a.rpm", "install.sh"]), "")
        (folder / "a.rpm").write_bytes(b"tampered")
        self.assertIn("does not match", updates.check_sums(folder, good, ["a.rpm", "install.sh"]))
        self.assertIn("not in the checksums", updates.check_sums(folder, "", ["a.rpm"]))


class DueTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "updates.json"
        patch = mock.patch.object(updates, "state_path", return_value=self.path)
        patch.start()
        self.addCleanup(patch.stop)

    def test_every_30_days_or_60(self):
        self.assertTrue(updates.due({}))  # never looked
        updates.mark_checked(now=1_000_000)
        self.assertFalse(updates.due({}, now=1_000_000 + 29 * 86400))
        self.assertTrue(updates.due({}, now=1_000_000 + 30 * 86400))
        self.assertFalse(updates.due({"update_days": 60}, now=1_000_000 + 45 * 86400))
        self.assertTrue(updates.due({"update_days": 90}, now=1_000_000 + 91 * 86400))
        self.assertFalse(updates.due({"update_days": 90}, now=1_000_000 + 75 * 86400))
        self.assertFalse(updates.due({"update_days": 0}, now=1_000_000 + 999 * 86400))  # off

    def test_off_looks_for_nothing_not_even_security_fixes(self):
        with mock.patch.object(updates.nas, "helper", lambda *a, **k: self.fail("must not look")), \
                mock.patch.object(updates, "open_terminal", lambda c: self.fail("must not open")):
            self.assertEqual(updates.auto({"update_days": 0}), 0)

    def test_a_security_fix_opens_the_terminal_even_when_not_due(self):
        updates.mark_checked()
        opened = []
        with mock.patch.object(updates.nas, "helper", return_value={"ok": True, "count": 4, "security": 2}), \
                mock.patch.object(updates, "open_terminal", lambda cmd: opened.append(cmd) or True):
            updates.auto({})
        self.assertEqual(opened[0][-2:], ["update", "--security"])
        opened.clear()
        with mock.patch.object(updates.nas, "helper", return_value={"ok": True, "count": 4, "security": 0}), \
                mock.patch.object(updates, "open_terminal", lambda cmd: opened.append(cmd) or True):
            updates.auto({})
        self.assertEqual(opened, [])


class HelperUpdateTests(HelperBase):
    CHECK = ("\nkernel.x86_64          5.14.0-611.el9      rhel-9-baseos\n"
             "samba.x86_64           4.21.3-4.el9        rhel-9-baseos\n"
             "Obsoleting Packages\nold.noarch  1-1  repo\n")
    SEC = ("RHSA-2026:1234 Important/Sec. kernel-5.14.0-611.el9.x86_64\n"
           "RHSA-2026:1234 Important/Sec. kernel-core-5.14.0-611.el9.x86_64\n"
           "RHSA-2026:1300 Moderate/Sec.  samba-4.21.3-4.el9.x86_64\n")

    def test_what_is_waiting_is_counted(self):
        self.rec.answers = {"dnf": (100, self.CHECK, "")}
        real = self.sys.run

        def run(args, stdin=None, timeout=120):
            if args[:2] == ["dnf", "-q"] and "updateinfo" in args:
                return 0, self.SEC, ""
            return real(args, stdin, timeout)

        self.sys.run = run
        result = self.job("updates", "check")[1]
        self.assertEqual((result["count"], result["security"], result["kernel"]), (2, 2, True))

    def test_no_software_sources_says_so(self):
        self.rec.answers = {"dnf": (1, "", "Error: Failed to download metadata for repo\n")}
        result = self.job("updates", "check")[1]
        self.assertFalse(result["ok"])
        self.assertIn("software sources", result["error"])


class WallpaperTests(unittest.TestCase):
    def test_the_corner_names_the_system(self):
        from PIL import Image

        out = Path(tempfile.mkdtemp()) / "wp.png"
        logo = Path(tempfile.mkdtemp()) / "logo.png"
        Image.new("RGBA", (64, 64), (200, 0, 0, 255)).save(logo)
        maintenance.make_wallpaper(out, host="doubletake", release={"PRETTY_NAME": "Rocky Linux 9.6"}, logo=logo)
        img = Image.open(out).convert("RGB")
        reds = sum(1 for x in range(0, 140) for y in range(960, 1080) if img.getpixel((x, y))[0] > 150)
        self.assertGreater(reds, 100)  # the logo is in the corner

    def test_the_systems_logo_is_found_where_the_desktop_keeps_it(self):
        root = tempfile.mkdtemp()
        icons = Path(root, "usr/share/icons/hicolor/256x256/apps")
        icons.mkdir(parents=True)
        (icons / "fedora-logo-icon.png").write_bytes(b"png")
        found = maintenance.system_logo({"LOGO": "fedora-logo-icon"}, roots=(root,))
        self.assertEqual(found.name, "fedora-logo-icon.png")
        self.assertIsNone(maintenance.system_logo({}, roots=(tempfile.mkdtemp(),), convert=lambda p: None))

    def test_the_corner_is_drawn_on_the_desktops_own_picture(self):
        from PIL import Image

        folder = Path(tempfile.mkdtemp())
        source, out = folder / "bokeh.jpg", folder / "out.jpg"
        Image.new("RGB", (3840, 2400), (0, 0, 0)).save(source)  # 16:10, on a 16:9 screen
        logo = folder / "logo.png"
        Image.new("RGBA", (64, 64), (200, 0, 0, 255)).save(logo)
        maintenance.footer_on(source, out, "tablet", release={"PRETTY_NAME": "Red Hat Enterprise Linux 9.8"},
                              logo=logo, screen=(1920, 1080))
        img = Image.open(out).convert("RGB")
        self.assertEqual(img.size, (1920, 1080))
        reds = [x for x in range(0, 300) for y in range(960, 1080, 2) if img.getpixel((x, y))[0] > 150]
        self.assertTrue(reds and 30 <= min(reds) <= 50)  # the logo 40 px in, as on the maintenance wallpaper
        self.assertEqual(img.getpixel((960, 540)), (0, 0, 0))  # the rest is the picture as it was

    def test_the_screen_size_comes_from_the_built_in_panel(self):
        root = Path(tempfile.mkdtemp())
        for name, status, modes in (("card1-DP-1", "connected", "3840x2160\n"), ("card1-eDP-1", "connected", "1920x1080\n1280x720\n"),
                                    ("card1-HDMI-A-1", "disconnected", "")):
            (root / name).mkdir()
            (root / name / "status").write_text(status + "\n")
            (root / name / "modes").write_text(modes)
        self.assertEqual(maintenance.screen_size(root), (1920, 1080))
        self.assertIsNone(maintenance.screen_size(Path(tempfile.mkdtemp())))

    def test_a_slideshow_wallpaper_gives_its_first_picture(self):
        folder = Path(tempfile.mkdtemp())
        pic = folder / "a.jpg"
        pic.write_bytes(b"x")
        xml = folder / "show.xml"
        xml.write_text(f"<background><static><file>{pic}</file></static></background>")
        self.assertEqual(maintenance._uri_file(f"'{xml.as_uri()}'"), pic)
        self.assertIsNone(maintenance._uri_file("'file:///nowhere.jpg'"))

    def test_the_quiet_desktop_keeps_its_own_wallpaper_and_gets_it_back(self):
        from PIL import Image

        folder = Path(tempfile.mkdtemp())
        source = folder / "bokeh.jpg"
        Image.new("RGB", (800, 500), (0, 0, 0)).save(source)
        settings = {"picture-uri": f"'{source.as_uri()}'"}

        def gsettings(*args):
            if args[0] == "range":
                return (0, "") if args[2] in settings else (1, "")
            if args[0] == "get":
                return 0, settings[args[2]]
            settings[args[2]] = args[3]
            return 0, ""

        footer = folder / "share" / "desktop-footer"
        with mock.patch.object(maintenance, "_gsettings", gsettings), \
                mock.patch.object(maintenance, "FOOTER", footer), \
                mock.patch.object(maintenance, "WALLPAPER", folder / "share" / "maintenance-wallpaper.png"), \
                mock.patch.object(maintenance, "flag_path", lambda: folder / "maintenance.json"):
            data = maintenance.footer_desktop({})
            self.assertIn("desktop-footer-light.jpg", settings["picture-uri"])
            self.assertEqual(data["saved"]["picture-uri"], f"'{source.as_uri()}'")
            maintenance._save(data)
            maintenance.finish()
        self.assertEqual(settings["picture-uri"], f"'{source.as_uri()}'")

    def test_the_quiet_desktop_follows_the_switch_not_the_name(self):
        with mock.patch.object(maintenance.nas, "system_state", return_value={"edition": "tron"}):
            self.assertTrue(maintenance.quiet())
        for state in ({}, {"edition": ""}):
            with mock.patch.object(maintenance.nas, "system_state", return_value=state), \
                    mock.patch.object(maintenance.socket, "gethostname", return_value="tron"):
                self.assertFalse(maintenance.quiet())



class TerminalTests(unittest.TestCase):
    def test_gnome_terminal_opens_big(self):
        started = []
        which = lambda name: "/usr/bin/" + name if name == "gnome-terminal" else None
        with mock.patch.object(updates.shutil, "which", which), \
                mock.patch.object(updates.subprocess, "Popen", lambda args, **kw: started.append(args)):
            self.assertTrue(updates.open_terminal(["doubletake", "update"]))
        self.assertEqual(started, [["gnome-terminal", "--geometry=80x24", "--zoom=1.35", "--", "doubletake", "update"]])


if __name__ == "__main__":
    unittest.main()
