"""The power mode, the power button, and how long the tablet has been left alone.

- The power mode (Power Saver, Balanced, Performance) is the system's own, set through its
  power profiles service (tuned-ppd on Rocky and Red Hat 9.8, power-profiles-daemon
  elsewhere): the same setting GNOME's Settings shows, no password needed.
- The power button: in DoubleTake it does what Settings says (sleep, or turn off), with no
  question asked. DoubleTake holds the button from the system while it runs (an inhibitor,
  as the desktop does), reads the press with the tablet's other keys, and during a backup
  waits until both copies are verified. On the Maintenance desktop, GNOME's own setting is
  kept in step.
- Left alone: the time since the last touch, from the desktop (Mutter), so that the screen
  goes dark and the tablet sleeps on the Maintenance desktop too, never while in use.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
from pathlib import Path

MODES = ("power-saver", "balanced", "performance")
BUTTON = ("sleep", "off")

# The power profiles service, under its old and its new D-Bus name.
_PROFILES = (("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles", "net.hadess.PowerProfiles"),
             ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles",
              "org.freedesktop.UPower.PowerProfiles"))


def _run(args, timeout=10) -> tuple:
    exe = shutil.which(args[0])
    if not exe:
        return 127, ""
    try:
        done = subprocess.run([exe] + list(args[1:]), capture_output=True, text=True, timeout=timeout)
        return done.returncode, done.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def get_mode() -> str:
    """The power mode now, or '' when this tablet has no power profiles service."""
    for dest, path, iface in _PROFILES:
        code, out = _run(["gdbus", "call", "--system", "--dest", dest, "--object-path", path, "--method",
                          "org.freedesktop.DBus.Properties.Get", iface, "ActiveProfile"])
        m = re.search(r"'([a-z-]+)'", out) if code == 0 else None
        if m and m.group(1) in MODES:
            return m.group(1)
    return ""


def set_mode(mode: str) -> bool:
    if mode not in MODES:
        return False
    for dest, path, iface in _PROFILES:
        code, _ = _run(["gdbus", "call", "--system", "--dest", dest, "--object-path", path, "--method",
                        "org.freedesktop.DBus.Properties.Set", iface, "ActiveProfile", f"<'{mode}'>"])
        if code == 0:
            return True
    return False


def idle_seconds() -> float | None:
    """How long since the last touch, key or mouse, as the desktop counts it; None when it
    cannot be asked (no Mutter on this session)."""
    code, out = _run(["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor",
                      "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
                      "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"], timeout=5)
    m = re.search(r"uint64\s+(\d+)", out) if code == 0 else None  # "(uint64 125000,)", milliseconds
    return int(m.group(1)) / 1000.0 if m else None


def act(action: str) -> bool:
    """Sleep, or turn off, now."""
    verb = {"sleep": "suspend", "off": "poweroff"}.get(action)
    if not verb:
        return False
    code, _ = _run(["systemctl", verb], timeout=30)
    return code == 0


def gnome_button(action: str) -> None:
    """GNOME's own power button setting, for the Maintenance desktop: sleep, or GNOME's power
    off question (GNOME has no "turn off straight away")."""
    value = {"sleep": "'suspend'", "off": "'interactive'"}.get(action)
    if value:
        _run(["gsettings", "set", "org.gnome.settings-daemon.plugins.power", "power-button-action", value])


def _die_with_parent() -> None:
    """For a helper process: end when DoubleTake ends, so it never outlives it holding the
    button (or sleep) from the system."""
    try:
        import ctypes

        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


class HoldButton:
    """Hold the power button from the system (logind) while DoubleTake runs, so a press comes
    to DoubleTake alone. Released when DoubleTake stops."""

    def __init__(self, start=None):
        self._proc = None
        self._start = start or self._spawn

    @staticmethod
    def _spawn():
        exe = shutil.which("systemd-inhibit")
        if not exe:
            return None
        return subprocess.Popen([exe, "--what=handle-power-key", "--who=DoubleTake",
                                 "--why=The power button does what DoubleTake's Settings say",
                                 "--mode=block", "sleep", "infinity"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                preexec_fn=_die_with_parent)

    @property
    def holding(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def hold(self) -> None:
        if not self.holding:
            try:
                self._proc = self._start()
            except OSError:
                self._proc = None

    def release(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def updating(proc_root: str = "/proc") -> bool:
    """A system update is running (dnf or rpm): never sleep in the middle of one."""
    try:
        names = os.listdir(proc_root)
    except OSError:
        return False
    for pid in names:
        if not pid.isdigit():
            continue
        try:
            comm = Path(proc_root, pid, "comm").read_text().strip()
        except OSError:
            continue
        if comm in ("dnf", "dnf-3", "dnf5", "yum", "rpm"):
            return True
    return False
