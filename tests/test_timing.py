"""doubletake camera-timing: read only, each part on a fresh connection, a report kept."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doubletake import timing  # noqa: E402


class Names:
    def __init__(self, names):
        self.names = list(names)

    def count(self):
        return len(self.names)

    def get_name(self, i):
        return self.names[i]


class FakeCam:
    """Two slots, each DCIM/100NCD85 (3 photos) and DCIM/101NCD85 (2 photos)."""

    def __init__(self, clock, log):
        self.clock, self.log = clock, log
        self.tree = {}
        for slot in ("/store_00010001", "/store_00020001"):
            self.tree[slot] = ([], ["DCIM"])
            self.tree[slot + "/DCIM"] = ([], ["100NCD85", "101NCD85"])
            self.tree[slot + "/DCIM/100NCD85"] = (["A.NEF", "B.NEF", "C.NEF"], [])
            self.tree[slot + "/DCIM/101NCD85"] = (["D.NEF", "E.NEF"], [])

    def get_storageinfo(self):
        return [SimpleNamespace(basedir="/store_00010001", description="XQD"),
                SimpleNamespace(basedir="/store_00020001", description="SD")]

    def folder_list_files(self, folder):
        self.log.append("list")
        return Names(self.tree[folder][0])

    def folder_list_folders(self, folder):
        return Names(self.tree[folder][1])

    def file_get_info(self, folder, name):
        self.log.append("info")
        self.clock.now += 0.15


class FakeSource:
    def __init__(self, clock, log):
        self.clock, self.log = clock, log
        self.cam = None

    def open(self):
        self.log.append("open")
        self.clock.now += 2
        self.cam = FakeCam(self.clock, self.log)

    def close(self):
        self.log.append("close")

    def info(self):
        return SimpleNamespace(model="Nikon D850", storage=["XQD (/store_00010001): 10 GB free of 64 GB"])


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="doubletake-timing-")
        self.addCleanup(shutil.rmtree, self.home, True)
        p = mock.patch.dict(os.environ, {"HOME": self.home})
        p.start()
        self.addCleanup(p.stop)

    def test_the_report(self):
        clock = SimpleNamespace(now=0.0)
        log = []
        t = timing.Timing(lambda: FakeSource(clock, log), say=lambda text: None, clock=lambda: clock.now)
        self.assertEqual(t.run(), 0)
        report = "\n".join(t.lines)
        self.assertIn("Camera:     Nikon D850", report)
        self.assertIn("Connecting: 2.0 s", report)
        self.assertIn("XQD                           5 photos in     0.0 s", report)  # names only
        self.assertIn("XQD                           5 photos in     0.8 s  (150 ms each)", report)
        self.assertIn("Both slots                   10 photos in     1.5 s  (150 ms each)", report)
        self.assertIn("The same question again, 10 photos", report)
        self.assertIn("Newest folder only (XQD, 101NCD85): 2 photos in 0.3 s", report)
        self.assertEqual(log.count("open"), 3)  # a fresh connection for each part
        self.assertEqual(log[-1], "close")
        saved = [f for f in os.listdir(self.home) if f.startswith("doubletake-camera-timing-")]
        self.assertEqual(len(saved), 1)

    def test_a_camera_that_goes_away_is_reported(self):
        def gone():
            raise RuntimeError("Could not claim the USB device")

        t = timing.Timing(gone, say=lambda text: None)
        self.assertEqual(t.run(), 1)
        self.assertIn("Stopped: Could not claim the USB device", t.lines)


if __name__ == "__main__":
    unittest.main()
