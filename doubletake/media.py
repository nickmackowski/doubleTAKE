"""The tablet's volume and brightness keys, in DoubleTake only.

The kiosk session runs no desktop handler for these keys, so they reached nothing. The
screens (Firefox, which has the keys while DoubleTake is up) pass them here instead: the
volume is changed through the system's sound service (PipeWire's wpctl, or pactl), and the
brightness through the desktop's power service, which the kiosk does run. No input device
is read for this, and nothing needs root.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from . import screen

KEYS = ("volume_up", "volume_down", "mute", "brightness_up", "brightness_down")
VOLUME_STEP = 5      # percent
BRIGHTNESS_STEP = 10  # percent, when the power service has no step of its own
SINK = "@DEFAULT_AUDIO_SINK@"


def _run(args) -> tuple:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return done.returncode, done.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def volume() -> dict:
    """{'level': 0..100, 'muted': bool}, or {} when no sound service answers."""
    if shutil.which("wpctl"):
        code, out = _run(["wpctl", "get-volume", SINK])
        m = re.search(r"Volume:\s*([0-9.]+)", out) if code == 0 else None
        if m:
            return {"level": round(float(m.group(1)) * 100), "muted": "MUTED" in out}
    if shutil.which("pactl"):
        code, out = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        m = re.search(r"(\d+)%", out) if code == 0 else None
        if m:
            code, mute = _run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
            return {"level": int(m.group(1)), "muted": "yes" in mute}
    return {}


def change_volume(key: str) -> dict:
    if shutil.which("wpctl"):
        if key == "mute":
            _run(["wpctl", "set-mute", SINK, "toggle"])
        else:
            if key == "volume_up":
                _run(["wpctl", "set-mute", SINK, "0"])
            _run(["wpctl", "set-volume", "-l", "1.0", SINK, f"{VOLUME_STEP}%{'+' if key == 'volume_up' else '-'}"])
    elif shutil.which("pactl"):
        if key == "mute":
            _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
        else:
            now = volume().get("level", 0)
            want = min(100, now + VOLUME_STEP) if key == "volume_up" else max(0, now - VOLUME_STEP)
            if key == "volume_up":
                _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
            _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{want}%"])
    return volume()


BACKLIGHT = Path("/sys/class/backlight")


def _backlight(root=None):
    """(name, now, max) of the screen's backlight, the panel's own one first, or None."""
    root = root or BACKLIGHT
    try:
        names = sorted(p.name for p in root.iterdir())
    except OSError:
        return None
    names.sort(key=lambda n: (not n.startswith("intel"), n))
    for name in names:
        try:
            now = int((root / name / "brightness").read_text())
            top = int((root / name / "max_brightness").read_text())
        except (OSError, ValueError):
            continue
        if top > 0:
            return name, now, top
    return None


def _login_brightness(key: str, root=None) -> dict:
    """Through the login service, which lets the user at the screen set its backlight."""
    found = _backlight(root)
    if not found or not shutil.which("busctl"):
        return {}
    name, now, top = found
    step = max(1, top * BRIGHTNESS_STEP // 100)
    want = min(top, now + step) if key == "brightness_up" else max(max(1, top // 100), now - step)
    code, _ = _run(["busctl", "call", "org.freedesktop.login1", "/org/freedesktop/login1/session/auto",
                    "org.freedesktop.login1.Session", "SetBrightness", "ssu", "backlight", name, str(want)])
    return {"level": round(100 * want / top)} if code == 0 else {}


def change_brightness(key: str) -> dict:
    """The power service's own step, or ten percent, never to fully black. Without the
    power service, through the login service."""
    method = "StepUp" if key == "brightness_up" else "StepDown"
    code, out = screen._gdbus("--method", f"{screen.IFACE}.{method}")
    level = None
    m = re.search(r"-?\d+", out) if code == 0 else None
    if m and 0 <= int(m.group()) <= 100:
        level = int(m.group())
    if level is None:
        now = screen.brightness()
        if now is None:
            return _login_brightness(key)
        level = min(100, now + BRIGHTNESS_STEP) if key == "brightness_up" else max(1, now - BRIGHTNESS_STEP)
        screen.set_brightness(level)
    if level == 0 and key == "brightness_down":
        screen.set_brightness(1)  # the screen stays readable; dark is the black cover's job
        level = 1
    return {"level": level}


BOUNCE = (25, 50, 75, 100)  # the 7212's rotate button: these levels, up to the top and back down


def next_bounce(now: int, going_up: bool) -> tuple:
    """The next level from `now`, and which way the press after it goes: at the top it turns
    down, at 25% up."""
    above = [x for x in BOUNCE if x > now]
    below = [x for x in BOUNCE if x < now]
    if going_up and not above:
        going_up = False
    elif not going_up and not below:
        going_up = True
    level = above[0] if going_up else below[-1]
    # At an end, the arrow shown already points the way the next press goes.
    if level == BOUNCE[-1]:
        going_up = False
    elif level == BOUNCE[0]:
        going_up = True
    return level, going_up


def bounce_brightness(going_up: bool, root=None, last: int | None = None) -> tuple:
    """One press of the rotate button: ({'level', 'kind', 'up'} or {'error'}, going_up).
    `last` is the level the previous press set, when it was moments ago: the power service
    may still report the old level, or round it (75 back as 74).
    The next level is worked out once, then set through the power service or, without one,
    the login service. (Worked out again for the second way, from the backlight and with the
    direction already turned, it went 50, 75, 50, 75 on the FZ-M1, which has no power
    service's brightness: Nick, 2026-11-25.)"""
    # A press soon after the last one goes on from where that one put it, without asking the
    # power service (slow on the 7212, and it may still report the old level).
    now = last if last is not None else screen.brightness()
    found = None
    if now is None:
        found = _backlight(root)  # no power service: the backlight itself
        if found:
            now = round(100 * found[1] / found[2])
    if now is None:
        return {"error": "The brightness could not be changed here."}, going_up
    level, after = next_bounce(now, going_up)
    if screen.set_brightness(level):
        return {"level": level, "kind": "brightness", "up": after}, after
    found = found or _backlight(root)  # through the login service, as the keys do
    if found and shutil.which("busctl"):
        name, _, top = found
        code, _ = _run(["busctl", "call", "org.freedesktop.login1", "/org/freedesktop/login1/session/auto",
                        "org.freedesktop.login1.Session", "SetBrightness", "ssu", "backlight", name,
                        str(max(1, top * level // 100))])
        if code == 0:
            return {"level": level, "kind": "brightness", "up": after}, after
    return {"error": "The brightness could not be changed here."}, going_up


def press(key: str) -> dict:
    """One press of a volume or brightness key: {'kind', 'level', 'muted'} for the screens'
    small level display, or {'error'}."""
    if key not in KEYS:
        return {"error": "Unknown key."}
    if key.startswith("brightness"):
        got = change_brightness(key)
        return dict(got, kind="brightness") if got else {"error": "The brightness could not be changed here."}
    got = change_volume(key)
    return dict(got, kind="volume") if got else {"error": "No sound service answered."}
