"""The screens' clock follows the desktop's 12- or 24-hour setting."""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doubletake import util  # noqa: E402


class ClockTests(unittest.TestCase):
    def setUp(self):
        util._CLOCK.update(at=0.0, format="24h")

    def test_follows_the_desktop(self):
        self.assertEqual(util.clock_format(now=1000, read=lambda: "'12h'\n"), "12h")
        util._CLOCK["at"] = 0
        self.assertEqual(util.clock_format(now=1000, read=lambda: "'24h'\n"), "24h")

    def test_24_hour_when_it_cannot_be_read(self):
        def broken():
            raise OSError("no gsettings")
        self.assertEqual(util.clock_format(now=1000, read=broken), "24h")

    def test_asked_once_a_minute_at_most(self):
        asked = []
        util.clock_format(now=1000, read=lambda: asked.append(1) or "'12h'")
        util.clock_format(now=1030, read=lambda: asked.append(1) or "'24h'")
        self.assertEqual(len(asked), 1)

    def test_how_it_reads(self):
        evening = time.mktime((2026, 10, 14, 21, 41, 0, 0, 0, -1))
        midnight = time.mktime((2026, 10, 15, 0, 5, 0, 0, 0, -1))
        self.assertEqual(util.clock_text(evening, "12h"), "9:41 PM")
        self.assertEqual(util.clock_text(midnight, "12h"), "12:05 AM")
        self.assertEqual(util.clock_text(evening, "24h"), "21:41")


if __name__ == "__main__":
    unittest.main()
