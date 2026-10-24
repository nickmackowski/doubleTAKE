"""DoubleTake only: the tablet logs in to a session that runs DoubleTake and nothing else.

The session is GNOME Kiosk, which comes with RHEL and Fedora (the gnome-kiosk-script-session
package). It shows one full-screen app, with no top bar, app list or desktop behind it,
and runs the user's ~/.local/bin/gnome-kiosk-script, which DoubleTake writes so that
it starts DoubleTake. Which session the tablet logs in to is kept by the system's
accounts service, and set through the helper.

The desktop is always one tap away: Settings, Device, Switch to the desktop.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import nas

SCRIPT = Path(os.path.expanduser("~/.local/bin/gnome-kiosk-script"))
MARK = "# Written by DoubleTake."
OLD_MARKS = ("# Written by Field Station.",)  # the same script, under the name used before 0.8.1
SESSION_DIRS = (Path("/usr/share/wayland-sessions"), Path("/usr/share/xsessions"))


def available(dirs=SESSION_DIRS) -> bool:
    """Is the kiosk session installed? Its file is gnome-kiosk-script.desktop on RHEL 9,
    and gnome-kiosk-script-wayland.desktop or -xorg.desktop in other versions."""
    return any(any(d.glob("gnome-kiosk-script*.desktop")) for d in dirs if d.is_dir())


def in_kiosk(env=None) -> bool:
    """Is this the kiosk session?"""
    env = os.environ if env is None else env
    if "1" in (env.get("DOUBLETAKE_KIOSK"), env.get("FIELDSTATION_KIOSK")):
        return True
    names = (env.get(k, "") for k in ("DESKTOP_SESSION", "XDG_SESSION_DESKTOP", "XDG_CURRENT_DESKTOP"))
    return any("kiosk" in n.lower() for n in names)


def started_by_desktop_autostart(env=None) -> bool:
    """In the kiosk session, a desktop autostart entry would open a second set of screens."""
    env = os.environ if env is None else env
    return in_kiosk(env) and "1" not in (env.get("DOUBLETAKE_KIOSK"), env.get("FIELDSTATION_KIOSK"))


def script_text(command: str) -> str:
    return (
        "#!/bin/sh\n"
        f"{MARK} The kiosk session runs this when the tablet logs in,\n"
        "# and logs in again if it ever ends. Switch back to the desktop in\n"
        "# DoubleTake: Settings, Device, Switch to the desktop.\n"
        "export DOUBLETAKE_KIOSK=1\n"
        f"exec {command} gui\n"
    )


def _command() -> str:
    found = shutil.which("doubletake")
    path = found or os.path.expanduser("~/.local/bin/doubletake")
    return "'" + path.replace("'", "'\\''") + "'"


def ours(path: Path) -> bool:
    """Is the script at this path one we wrote, under either name?"""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return False
    return any(mark in text for mark in (MARK,) + OLD_MARKS)


def write_script(path: Path = SCRIPT, command: str | None = None) -> str:
    """Write the kiosk script. One that is not ours is kept beside it. Returns an error, or ''."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and not ours(path):
            path.replace(path.with_name(path.name + ".before-doubletake"))
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(script_text(command or _command()), encoding="utf-8")
        tmp.chmod(0o755)
        tmp.replace(path)
    except OSError as exc:
        return f"Could not write {path}: {exc}"
    return ""


def remove_script(path: Path = SCRIPT) -> None:
    """Remove our kiosk script, and put back one that was there before."""
    try:
        if path.is_file() and ours(path):
            path.unlink()
        before = path.with_name(path.name + ".before-doubletake")
        if before.is_file() and not path.exists():
            before.replace(path)
    except OSError:
        pass


def switch(on: bool, script: Path | None = None) -> str:
    """Log in to DoubleTake only from now on, or to the desktop. Returns an error, or ''."""
    script = script or SCRIPT
    if on:
        if not available():
            return ("The kiosk session is not installed. Run doubletake setup in a terminal, which adds it "
                    "(the gnome-kiosk-script-session package).")
        error = write_script(script)
        if error:
            return error
    answer = nas.helper("session", "kiosk" if on else "desktop")
    if not answer.get("ok"):
        if on:
            remove_script(script)
        return answer.get("error", "The login session could not be changed.")
    if not on:
        remove_script(script)
    return ""


def chosen() -> str:
    """'kiosk' or 'desktop', as last set through the helper."""
    return "kiosk" if nas.system_state().get("session") == "kiosk" else "desktop"


def _run(args, timeout=30) -> str:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return "" if done.returncode == 0 else (done.stderr.strip() or done.stdout.strip() or "it failed")


def power(action: str) -> str:
    """'restart' or 'shutdown' the tablet. Returns an error, or ''."""
    verb = {"restart": "reboot", "shutdown": "poweroff"}.get(action)
    if not verb:
        return "Unknown action."
    error = _run(["systemctl", verb])
    return f"The tablet would not {action.replace('shutdown', 'shut down')}: {error}" if error else ""


def log_out() -> str:
    """End this login, so the login screen starts the session that is now chosen."""
    session_id = os.environ.get("XDG_SESSION_ID", "")
    if shutil.which("gnome-session-quit") and not _run(["gnome-session-quit", "--logout", "--no-prompt"]):
        return ""
    if session_id:
        error = _run(["loginctl", "terminate-session", session_id])
        return f"Could not log out: {error}" if error else ""
    return "Could not log out. Restart the tablet instead."
