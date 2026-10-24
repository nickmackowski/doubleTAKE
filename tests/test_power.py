"""Tests for the battery rules."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doubletake import power  # noqa: E402


class PowerTests(unittest.TestCase):
    def test_percent(self):
        self.assertEqual([power.percent(v) for v in (80, "80%", " 7 % ", "", None, True, "Full")],
                         [80, 80, 7, None, None, None, None])

    def test_camera_stops_at_ten(self):
        self.assertEqual(power.camera_problem("11%"), "")
        self.assertIn("10%", power.camera_problem("10%"))
        self.assertEqual(power.camera_problem(""), "")  # not known: never blocks

    def test_tablet_only_off_the_charger(self):
        self.assertIn("Plug it in", power.tablet_problem(9, False))
        self.assertEqual(power.tablet_problem(9, True), "")
        self.assertEqual(power.tablet_problem(None, False), "")

    def test_camera_comes_first(self):
        self.assertIn("camera", power.problem("5%", 5, False))


if __name__ == "__main__":
    unittest.main()
