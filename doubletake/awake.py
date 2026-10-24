"""Keep the tablet from going to sleep while photos are being copied.

On battery the desktop puts the tablet to sleep after a while with no touch, which
would pause a long copy halfway. While a backup, the second copy or a countdown is
under way, DoubleTake holds the system's sleep off (a standard "inhibitor", the
same thing a video player uses), and lets go as soon as it is done.
"""

from __future__ import annotations

import shutil
import subprocess


class StayAwake:
    def __init__(self, start=None):
        self._proc = None
        self._start = start or self._spawn

    @staticmethod
    def _spawn():
        exe = shutil.which("systemd-inhibit")
        if not exe:
            return None
        return subprocess.Popen([exe, "--what=sleep", "--who=DoubleTake", "--why=Copying photos",
                                 "--mode=block", "sleep", "86400"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)

    @property
    def holding(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def hold(self, busy: bool) -> None:
        if busy and not self.holding:
            try:
                self._proc = self._start()
            except OSError:
                self._proc = None
        elif not busy and self._proc is not None:
            try:
                self._proc.terminate()
            except OSError:
                pass
            self._proc = None
