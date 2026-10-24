"""The volume and brightness keys, passed on by the screens."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doubletake import media  # noqa: E402


class VolumeTests(unittest.TestCase):
    def setUp(self):
        self.ran, self.level, self.muted = [], 0.40, False

        def run(args):
            self.ran.append(args)
            if args[:2] == ["wpctl", "get-volume"]:
                return 0, f"Volume: {self.level:.2f}" + (" [MUTED]" if self.muted else "") + "\n"
            if args[:2] == ["wpctl", "set-volume"]:
                step = 0.05 if args[-1].endswith("+") else -0.05
                self.level = max(0.0, min(1.0, self.level + step))
            if args[:3] == ["wpctl", "set-mute", media.SINK]:
                self.muted = (not self.muted) if args[3] == "toggle" else args[3] == "1"
            return 0, ""

        for patch in (mock.patch.object(media, "_run", run),
                      mock.patch.object(media.shutil, "which", lambda name: "/usr/bin/" + name if name == "wpctl" else None)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_up_down_and_mute(self):
        self.assertEqual(media.press("volume_up"), {"kind": "volume", "level": 45, "muted": False})
        self.assertEqual(media.press("volume_down")["level"], 40)
        self.assertTrue(media.press("mute")["muted"])
        self.assertFalse(media.press("volume_up")["muted"])  # turning it up unmutes
        self.assertIn(["wpctl", "set-volume", "-l", "1.0", media.SINK, "5%+"], self.ran)  # never above 100%

    def test_unknown_key(self):
        self.assertIn("error", media.press("format_card"))


class BrightnessTests(unittest.TestCase):
    def test_the_power_service_steps(self):
        with mock.patch.object(media.screen, "_gdbus", lambda *a: (0, "(70, 'eDP-1')\n")):
            self.assertEqual(media.press("brightness_up"), {"kind": "brightness", "level": 70})

    def test_never_to_black(self):
        set_to = []
        with mock.patch.object(media.screen, "_gdbus", lambda *a: (0, "(0, 'eDP-1')\n")), \
                mock.patch.object(media.screen, "set_brightness", lambda v: set_to.append(v) or True):
            self.assertEqual(media.press("brightness_down")["level"], 1)
        self.assertEqual(set_to, [1])

    def test_without_the_power_service_the_login_service(self):
        import tempfile

        root = Path(tempfile.mkdtemp())
        for name, now, top in (("acpi_video0", 5, 10), ("intel_backlight", 50000, 100000)):
            (root / name).mkdir()
            (root / name / "brightness").write_text(f"{now}\n")
            (root / name / "max_brightness").write_text(f"{top}\n")
        ran = []
        with mock.patch.object(media.screen, "_gdbus", lambda *a: (1, "")), \
                mock.patch.object(media.screen, "brightness", lambda: None), \
                mock.patch.object(media, "BACKLIGHT", root), \
                mock.patch.object(media.shutil, "which", lambda n: "/usr/bin/" + n), \
                mock.patch.object(media, "_run", lambda a: ran.append(a) or (0, "")):
            got = media.change_brightness("brightness_up")
        self.assertEqual(got, {"level": 60})  # the panel's own backlight, a tenth up
        self.assertEqual(ran[-1][-3:], ["backlight", "intel_backlight", "60000"])

    def test_without_a_step_ten_percent(self):
        set_to = []
        with mock.patch.object(media.screen, "_gdbus", lambda *a: (1, "")), \
                mock.patch.object(media.screen, "brightness", lambda: 50), \
                mock.patch.object(media.screen, "set_brightness", lambda v: set_to.append(v) or True):
            self.assertEqual(media.press("brightness_up")["level"], 60)
        self.assertEqual(set_to, [60])


if __name__ == "__main__":
    unittest.main()
