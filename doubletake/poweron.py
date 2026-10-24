"""Start at power-on: the tablet logs in by itself and opens DoubleTake full screen.

Three parts, switched together:
  1. Automatic login, with no password, set in the login screen by the system helper.
  2. DoubleTake opening when the desktop starts (the autostart entry).
  3. No lock screen and no screen blanking, so a long copy is never hidden behind a
     password. The desktop's own values are kept, and put back when it is switched off.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import config, nas, settings

GDM_CONF = Path("/etc/gdm/custom.conf")

# (schema, key, value while on). Only these are changed, and only while switched on.
DESKTOP = [
    ("org.gnome.desktop.screensaver", "lock-enabled", "false"),
    ("org.gnome.desktop.screensaver", "ubuntu-lock-on-suspend", "false"),
    ("org.gnome.desktop.session", "idle-delay", "uint32 0"),
    ("org.gnome.settings-daemon.plugins.power", "sleep-inactive-ac-type", "'nothing'"),
    # DoubleTake decides when the screen goes dark and when the tablet sleeps on battery
    # (Settings, Display), the same in the kiosk and on the desktop.
    ("org.gnome.settings-daemon.plugins.power", "sleep-inactive-battery-type", "'nothing'"),
    ("org.gnome.desktop.notifications", "show-in-lock-screen", "false"),
]


def autologin_user() -> str:
    """Who the login screen logs in by itself, or '' when nobody."""
    try:
        text = GDM_CONF.read_text()
    except OSError:
        return ""
    section, enabled, user = "", False, ""
    for line in text.splitlines():
        bare = line.strip()
        if bare.startswith("[") and bare.endswith("]"):
            section = bare[1:-1].strip().lower()
        elif section == "daemon" and "=" in bare and not bare.startswith("#"):
            key, value = (x.strip() for x in bare.split("=", 1))
            if key == "AutomaticLoginEnable":
                enabled = value.lower() in ("true", "1", "yes")
            elif key == "AutomaticLogin":
                user = value
    return user if enabled else ""


def _gsettings(*args) -> tuple:
    exe = shutil.which("gsettings")
    if not exe:
        return 127, ""
    try:
        done = subprocess.run([exe] + list(args), capture_output=True, text=True, timeout=10)
        return done.returncode, done.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _keys():
    """The desktop keys this tablet has (an older or trimmed desktop may lack some)."""
    for schema, key, value in DESKTOP:
        code, _ = _gsettings("range", schema, key)
        if code == 0:
            yield schema, key, value


def is_on(user: str) -> bool:
    return autologin_user() == user and settings.AUTOSTART.is_file()


def refresh(cfg: dict, user: str) -> None:
    """While switched on, make sure every desktop setting this version manages is in place.

    A newer version may manage one more (as 0.7.9.4 does with sleep on battery); the
    desktop's own value is kept, to be put back when start at power-on is switched off.
    """
    if not is_on(user):
        return
    saved = dict(cfg.get("desktop_saved") or {})
    changed = False
    for schema, key, value in _keys():
        name = schema + " " + key
        if name not in saved:
            code, current = _gsettings("get", schema, key)
            if code == 0:
                saved[name] = current
                changed = True
            _gsettings("set", schema, key, value)
    if changed:
        config.save({"desktop_saved": saved})
        cfg["desktop_saved"] = saved


def switch(cfg: dict, on: bool, user: str) -> str:
    """Switch all three parts on or off. Returns an error to show, or ''."""
    problems = []
    answer = nas.helper("autologin", "on" if on else "off")
    if not answer.get("ok"):
        problems.append("automatic login: " + answer.get("error", "refused"))
        if on:
            return "Could not switch on " + problems[0] + "."
    error = settings.set_autostart(on)
    if error:
        problems.append(error)
    saved = dict(cfg.get("desktop_saved") or {})
    if on:
        for schema, key, value in _keys():
            name = schema + " " + key
            if name not in saved:
                code, current = _gsettings("get", schema, key)
                if code == 0:
                    saved[name] = current
            _gsettings("set", schema, key, value)
        config.save({"desktop_saved": saved})
        cfg["desktop_saved"] = saved
    else:
        for name, value in saved.items():
            schema, key = name.split(" ", 1)
            _gsettings("set", schema, key, value)
        config.save({"desktop_saved": {}})
        cfg["desktop_saved"] = {}
    return "; ".join(problems)
