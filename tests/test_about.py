"""Settings > Device > About: the tablet's parts, read the way any user can, and what the helper
wrote at setup (serial number, memory details)."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doubletake import settings  # noqa: E402


class AboutTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="doubletake-about-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "proc"))

    def proc(self, name, text):
        with open(os.path.join(self.root, "proc", name), "w") as fh:
            fh.write(text)

    def test_processor(self):
        cpu = ("processor\t: {n}\nmodel name\t: Intel(R) Core(TM) i7-8650U CPU @ 1.90GHz\n"
               "cpu cores\t: 4\n\n")
        self.proc("cpuinfo", "".join(cpu.format(n=n) for n in range(8)))
        self.assertEqual(settings._processor(self.root), "Intel Core i7-8650U, 4 cores, 8 threads")

    def test_memory_is_the_size_fitted(self):
        self.proc("meminfo", "MemTotal:       16131072 kB\nMemFree: 1 kB\n")  # 15.4 GiB seen
        self.assertEqual(settings._memory(self.root), "16 GB")

    def test_memory_and_serial_from_what_setup_wrote(self):
        path = os.path.join(self.root, "tablet.json")
        with open(path, "w") as fh:
            json.dump({"serial": "7XQ2K93", "vendor": "Dell Inc.", "memory_type": "DDR4",
                       "memory_speed": "2400 MT/s", "memory_maker": "Samsung"}, fh)
        details = settings._tablet_details(path)
        self.assertEqual(settings._memory_text("16 GB", details), "16 GB DDR4, 2400 MT/s, Samsung")
        self.assertEqual(settings._serial_text(details), "7XQ2K93 (Dell service tag)")
        self.assertEqual(settings._serial_text({"serial": "5KTSA12345", "vendor": "Panasonic Corporation"}),
                         "5KTSA12345")

    def test_nothing_written_yet(self):
        details = settings._tablet_details(os.path.join(self.root, "missing.json"))
        self.assertEqual(details, {})
        self.assertEqual(settings._memory_text("16 GB", details), "16 GB")
        self.assertEqual(settings._serial_text(details), "")

    def sys_files(self, base, files):
        for name, text in files.items():
            path = os.path.join(self.root, base, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(text + "\n")

    def test_copy2_a_microsd_card(self):
        self.sys_files("sys/block/mmcblk0", {"size": str(500107862016 // 512), "device/manfid": "0x000003",
                                              "device/name": "SN512", "device/type": "SD",
                                              "device/date": "03/2025"})
        self.assertEqual(settings.copy2_hardware("/dev/mmcblk0p1", self.root),
                         "SanDisk SN512 microSD, 500 GB, made 03/2025")

    def test_copy2_a_card_from_an_unknown_maker(self):
        self.sys_files("sys/block/mmcblk0", {"size": str(64 * 10**9 // 512), "device/manfid": "0x0000aa",
                                              "device/name": "ABCDE", "device/type": "SD", "device/date": "nonsense"})
        self.assertEqual(settings.copy2_hardware("/dev/mmcblk0p1", self.root), "ABCDE microSD, 64 GB")

    def test_copy2_a_usb_drive_and_its_speed(self):
        usb = "sys/devices/pci0000:00/usb2/2-1"
        self.sys_files(usb, {"speed": "10000", "idVendor": "04e8"})
        disk = os.path.join(usb, "2-1:1.0/host0/target0/0:0:0:0/block/sda")
        self.sys_files(disk, {"size": str(10**12 // 512), "device/vendor": "Samsung ", "device/model": "PSSD T7  "})
        os.makedirs(os.path.join(self.root, "sys/block"), exist_ok=True)
        os.symlink(os.path.join(self.root, disk), os.path.join(self.root, "sys/block/sda"))
        self.assertEqual(settings.copy2_hardware("/dev/sda1", self.root),
                         "Samsung PSSD T7, 1.0 TB, USB 3 (10 Gb/s)")

    def test_copy2_nothing_known(self):
        self.assertEqual(settings.copy2_hardware("", self.root), "")
        self.assertEqual(settings.copy2_hardware("/dev/mmcblk9p1", self.root), "microSD")

    def test_size_text(self):
        self.assertEqual(settings._size_text(1024209543168), "1.0 TB")
        self.assertEqual(settings._size_text(128035676160), "128 GB")


if __name__ == "__main__":
    unittest.main()
