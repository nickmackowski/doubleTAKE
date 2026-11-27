"""doubletake camera-timing: where the wait before copying goes, with a camera over USB.

Read only: it asks the camera for its folders, its photos' names, and each photo's details
(size and time), the same questions DoubleTake asks before a backup. It never reads a photo,
and never writes anything to the camera. Each part starts from a fresh connection, so the
camera library's memory of an earlier part does not make a later one look fast.

  1. Connecting.
  2. Folders and names only, each card slot on its own.
  3. As DoubleTake does it: names plus a question about each photo, each slot on its own.
     Then the same questions again, to see whether the answers were remembered.
  4. The newest folder of the first slot only.

The report is printed and kept in a text file in the home folder, to send on.
"""

from __future__ import annotations

import os
import time
from datetime import datetime


def _ms(seconds: float, count: int) -> str:
    return f"{seconds * 1000 / count:.0f} ms each" if count else "no photos"


def _secs(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.1f} s"
    if seconds < 120:
        return f"{seconds:.0f} s"
    return f"{seconds // 60:.0f} min {seconds % 60:02.0f} s"


class Timing:
    def __init__(self, make_source, say=print, clock=time.monotonic):
        self.make_source = make_source
        self.say = say
        self.clock = clock
        self.lines: list[str] = []
        self.src = None

    def out(self, text: str = "") -> None:
        self.lines.append(text)
        self.say(text)

    # ----- the camera -----------------------------------------------------

    def connect(self) -> float:
        if self.src is not None:
            self.src.close()
        t0 = self.clock()
        self.src = self.make_source()
        self.src.open()
        return self.clock() - t0

    def slots(self) -> list[tuple[str, str]]:
        """(folder, name) for each card slot the camera shows."""
        found = []
        try:
            for st in self.src.cam.get_storageinfo():
                label = (getattr(st, "description", "") or getattr(st, "label", "") or "").strip()
                if st.basedir:
                    found.append((st.basedir, label or st.basedir))
        except Exception:
            pass
        return found or [("/", "the camera")]

    def walk(self, top: str, ask_each: bool, only=None) -> tuple[int, list]:
        """Photos under top (in DCIM when there is one): (count, [(folder, name)])."""
        cam = self.src.cam
        found = []

        def go(folder, depth=0):
            if depth > 8:
                return
            files = cam.folder_list_files(folder)
            for i in range(files.count()):
                name = files.get_name(i)
                if name.startswith("."):
                    continue
                if ask_each:
                    cam.file_get_info(folder, name)
                found.append((folder, name))
            subs = cam.folder_list_folders(folder)
            for i in range(subs.count()):
                go(folder.rstrip("/") + "/" + subs.get_name(i), depth + 1)

        go(only or top)
        dcim = [f for f in found if "/DCIM/" in f[0] + "/"]
        found = dcim or found
        return len(found), found

    def newest_folder(self, top: str) -> str:
        """The last photo folder under DCIM (105ND850 after 104ND850), or ''."""
        cam = self.src.cam
        dcim = top.rstrip("/") + "/DCIM"
        try:
            subs = cam.folder_list_folders(dcim)
        except Exception:
            return ""
        names = sorted(subs.get_name(i) for i in range(subs.count()))
        return dcim + "/" + names[-1] if names else ""

    # ----- the test -------------------------------------------------------

    def run(self) -> int:
        stamp = datetime.now().strftime("%Y-%m-%d %I:%M %p")
        self.out(f"DoubleTake camera timing, {stamp}")
        self.out("Read only: nothing is written to the camera, and no photo is read.")
        self.out()
        try:
            took = self.connect()
            info = self.src.info()
            self.out(f"Camera:     {info.model}")
            for row in info.storage or []:
                self.out(f"Card:       {row}")
            self.out(f"Connecting: {_secs(took)}")
            slots = self.slots()
            self.out()

            self.out("Folders and names only (no question per photo):")
            names = {}
            for top, label in slots:
                t0 = self.clock()
                count, found = self.walk(top, ask_each=False)
                spent = self.clock() - t0
                names[top] = found
                self.out(f"  {label:<24} {count:>6,} photos in {_secs(spent):>9}  ({_ms(spent, count)})")
            self.out()

            self.out("As DoubleTake does it (names, plus a question about each photo):")
            self.connect()
            total_t, total_n = 0.0, 0
            for top, label in slots:
                t0 = self.clock()
                count, found = self.walk(top, ask_each=True)
                spent = self.clock() - t0
                total_t += spent
                total_n += count
                self.out(f"  {label:<24} {count:>6,} photos in {_secs(spent):>9}  ({_ms(spent, count)})")
            if len(slots) > 1:
                self.out(f"  {'Both slots':<24} {total_n:>6,} photos in {_secs(total_t):>9}  ({_ms(total_t, total_n)})")
            # The same questions again, on the same connection: remembered, or asked again?
            again = [f for found in names.values() for f in found][:100]
            if again:
                t0 = self.clock()
                for folder, name in again:
                    self.src.cam.file_get_info(folder, name)
                spent = self.clock() - t0
                self.out(f"  The same question again, {len(again)} photos: {_secs(spent)} ({_ms(spent, len(again))})")
            self.out()

            top, label = slots[0]
            self.connect()
            t0 = self.clock()
            newest = self.newest_folder(top)
            if newest:
                count, _ = self.walk(top, ask_each=True, only=newest)
                spent = self.clock() - t0
                self.out(f"Newest folder only ({label}, {newest.rsplit('/', 1)[-1]}): "
                         f"{count:,} photos in {_secs(spent)} ({_ms(spent, count)})")
            else:
                self.out("Newest folder only: no DCIM folder found.")
        except Exception as exc:  # the camera library's own errors too
            self.out(f"Stopped: {exc}")
            self.save()
            return 1
        finally:
            if self.src is not None:
                self.src.close()
        self.save()
        return 0

    def save(self) -> None:
        path = os.path.expanduser(datetime.now().strftime("~/doubletake-camera-timing-%Y%m%d-%H%M.txt"))
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(self.lines) + "\n")
            self.say(f"\nSaved to {path}")
        except OSError:
            pass
