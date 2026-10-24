"""The tablet's own keys: P1, P2 and P3 on the Dell Latitude 7220 Rugged Extreme.

The helper reads them (input devices need root) and prints one line per press; this
listens to it and hands each press to the station:

  P1  one-touch backup: the new photos, now; one beep at the start, two when done
  P2  night red on and off, in the look in use
  P3  the tablet's status: version, photos on Copy 1 and Copy 2, the batteries

and the screen's brightness keys, which in DoubleTake only reach no desktop handler.

On a tablet without these keys the helper says so and stops, and nothing more is tried.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time

from . import nas

RETRY = 60.0  # seconds before listening again, when the helper stopped for no clear reason


class KeyWatcher:
    def __init__(self, on_key, command=None, retry: float = RETRY):
        self.on_key = on_key
        self.command = command or ["sudo", "-n", nas.HELPER, "keys"]
        self.retry = retry
        self.stopping = threading.Event()
        self.process = None
        self.thread = None
        self.none_here = False  # the tablet has no P keys

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="keys", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        process = self.process
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass

    def _run(self) -> None:
        while not self.stopping.is_set():
            try:
                self.process = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                text=True, bufsize=1)
            except OSError:
                return
            with self.process.stdout:
                for line in self.process.stdout:
                    if self.stopping.is_set():
                        break
                    self._line(line)
            self.process.wait()
            if self.none_here or self.stopping.wait(self.retry):
                return

    def _line(self, line: str) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            return
        if message.get("key") in ("p1", "p2", "p3", "brightness_up", "brightness_down"):
            try:
                self.on_key(message["key"])
            except Exception:
                pass  # one key press going wrong never stops the listening
        elif message.get("ok") is False:
            self.none_here = True


def wait_until(predicate, seconds: float = 5.0) -> bool:
    """For tests: wait for something the listening thread does."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()
