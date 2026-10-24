"""Tests for reading the tablet's batteries, one or two."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doubletake import station  # noqa: E402


class TabletBatteryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="doubletake-power-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def supply(self, name, **values):
        d = self.root / name
        d.mkdir()
        for key, value in values.items():
            (d / key).write_text(f"{value}\n")

    def test_two_batteries_are_taken_together(self):
        # As on the 5175: one battery full, one nearly empty, on the charger.
        self.supply("BAT0", type="Battery", status="Full", present=1, capacity=100,
                    energy_now=21090000, energy_full=21090000)
        self.supply("BAT1", type="Battery", status="Discharging", present=1, capacity=10,
                    energy_now=1864000, energy_full=18640000)
        self.supply("AC", type="Mains", online=1)
        got = station._tablet_battery(str(self.root))
        self.assertEqual((got["battery"], got["batteries"], got["on_power"]), (58, 2, True))
        self.assertEqual([(x["label"], x["level"]) for x in got["packs"]], [("Battery 1", 100), ("Battery 2", 10)])
        self.assertEqual(got["swap"], "")  # on the charger: nothing to swap

    def test_says_which_one_to_swap(self):
        self.supply("BAT0", type="Battery", status="Discharging", present=1, capacity=80,
                    energy_now=16000000, energy_full=20000000, energy_full_design=25000000)
        self.supply("BAT1", type="Battery", status="Discharging", present=1, capacity=8,
                    energy_now=1600000, energy_full=20000000, energy_full_design=25000000)
        self.supply("AC", type="Mains", online=0)
        got = station._tablet_battery(str(self.root))
        self.assertEqual((got["battery"], got["swap"]), (44, "Battery 2"))
        self.assertEqual(got["packs"][0]["health"], 80)

    def test_cycles_and_time_left(self):
        # Two packs on battery, 30 Wh left between them, drawing 10 W: three hours.
        self.supply("BAT0", type="Battery", status="Discharging", present=1, capacity=50,
                    energy_now=20000000, energy_full=40000000, power_now=6000000, cycle_count=112)
        self.supply("BAT1", type="Battery", status="Discharging", present=1, capacity=25,
                    energy_now=10000000, energy_full=40000000, power_now=4000000, cycle_count=0)
        self.supply("AC", type="Mains", online=0)
        got = station._tablet_battery(str(self.root))
        self.assertEqual([x["cycles"] for x in got["packs"]], [112, None])  # 0: the battery does not say
        self.assertEqual(got["minutes"], 180)

    def test_a_bay_left_empty_during_a_swap(self):
        self.supply("BAT0", type="Battery", status="Discharging", present=1, capacity=40,
                    charge_now=1600000, charge_full=4000000)
        self.supply("BAT1", type="Battery", status="Unknown", present=0)
        self.supply("AC", type="Mains", online=0)
        got = station._tablet_battery(str(self.root))
        self.assertEqual((got["battery"], got["batteries"], got["on_power"]), (40, 1, False))

    def test_the_keyboard_charging_the_tablet_is_not_the_charger(self):
        # The 5175 unplugged: the keyboard's battery charges the tablet's.
        self.supply("BAT0", type="Battery", status="Charging", present=1, capacity=60,
                    energy_now=12000000, energy_full=20000000)
        self.supply("BAT1", type="Battery", status="Discharging", present=1, capacity=50,
                    energy_now=9000000, energy_full=18000000)
        self.supply("AC", type="Mains", online=0)
        self.assertFalse(station._tablet_battery(str(self.root))["on_power"])
        (self.root / "AC" / "online").write_text("1\n")
        self.assertTrue(station._tablet_battery(str(self.root))["on_power"])

    def test_no_charger_entry_goes_by_the_battery(self):
        self.supply("BAT0", type="Battery", status="Charging", present=1, capacity=60)
        self.assertTrue(station._tablet_battery(str(self.root))["on_power"])

    def test_a_pen_is_not_the_tablet(self):
        self.supply("hid-pen-battery", type="Battery", scope="Device", status="Discharging", capacity=5)
        self.assertEqual(station._tablet_battery(str(self.root)), {})


if __name__ == "__main__":
    unittest.main()


class StayAwakeTests(unittest.TestCase):
    def test_holds_sleep_off_only_while_busy(self):
        from doubletake import awake

        made = []

        class Proc:
            def __init__(self):
                self.alive = True

            def poll(self):
                return None if self.alive else 0

            def terminate(self):
                self.alive = False

        def start():
            made.append(Proc())
            return made[-1]

        keeper = awake.StayAwake(start)
        keeper.hold(True)
        keeper.hold(True)  # once is enough
        self.assertEqual(len(made), 1)
        self.assertTrue(keeper.holding)
        keeper.hold(False)
        self.assertFalse(made[0].alive)
        self.assertFalse(keeper.holding)
