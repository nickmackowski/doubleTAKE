"""The tablet's screen: dark between backups, lit again by a touch or the camera.

"Dark" is the backlight turned right down, with the screens showing a black cover. The
panel itself is never powered off: when the desktop's display service (Mutter) powers a
panel off, it switches its touchscreen off too, and does not always switch it back on,
which left touch dead until a restart. With the panel kept on, a touch reaches Field
Station; the first touch only lights the screen and presses nothing.

The backlight is set through the desktop's power service (gsd-power), which both the
desktop and the kiosk session run. Without it, the black cover alone still darkens.
"""

from __future__ import annotations

import re
import shutil
import subprocess

DEST = "org.gnome.SettingsDaemon.Power"
PATH = "/org/gnome/SettingsDaemon/Power"
IFACE = "org.gnome.SettingsDaemon.Power.Screen"
DARK_LEVEL = 0  # percent; the power service keeps the panel lit at its lowest step

_saved: dict = {}


def _gdbus(*args) -> tuple:
    exe = shutil.which("gdbus")
    if not exe:
        return 1, ""
    try:
        r = subprocess.run([exe, "call", "--session", "--dest", DEST, "--object-path", PATH, *args],
                           capture_output=True, text=True, timeout=10)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def brightness() -> int | None:
    code, out = _gdbus("--method", "org.freedesktop.DBus.Properties.Get", IFACE, "Brightness")
    m = re.search(r"-?\d+", out) if code == 0 else None
    value = int(m.group()) if m else None
    return value if value is not None and 0 <= value <= 100 else None


def set_brightness(level: int) -> bool:
    level = max(0, min(100, int(level)))
    return _gdbus("--method", "org.freedesktop.DBus.Properties.Set", IFACE, "Brightness",
                  f"<int32 {level}>")[0] == 0


def set_power(on: bool) -> bool:
    """Light the screen, or darken it. Always True: the screens' black cover does the darkening,
    and the backlight is turned down with it when the power service is there."""
    if not on:
        level = brightness()
        if level is not None and "level" not in _saved:
            _saved["level"] = level
        set_brightness(DARK_LEVEL)
    else:
        level = _saved.pop("level", None)
        if level is not None:
            set_brightness(max(level, 10))
    return True


def suspend() -> bool:
    """Put the tablet to sleep, as the desktop would on battery."""
    exe = shutil.which("systemctl")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "suspend"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
