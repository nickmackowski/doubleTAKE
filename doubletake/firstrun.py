"""The first start after installing the DoubleTake package (RPM).

The package puts the program in /usr/share/doubletake and the helper in /usr/libexec for
every user of the computer. What belongs to the tablet's own user is done here, the first
time DoubleTake starts:

- as the user: the desktop is stopped from grabbing the camera, and what an earlier
  install by setup.sh left in the home folder (its own copy of the program, its command
  and app-list entry) is tidied away, with the start-up entries pointed at the package;
- once, in a terminal with the tablet's password (install.sh, or doubletake setup): the
  rule that lets the screens run the helper, the NAS, the boot menu entry, and the
  DoubleTake boot screen (on by default).

An install by setup.sh does all of this itself, so none of it runs there.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

PACKAGE_HELPER = Path("/usr/libexec/doubletake-helper")
PACKAGE_HOME = Path("/usr/share/doubletake")


def packaged() -> bool:
    """Is this DoubleTake the one installed from the package?"""
    here = Path(__file__).resolve()
    return PACKAGE_HELPER.is_file() and PACKAGE_HOME in here.parents


def helper_allowed() -> bool:
    """May this user run the helper without a password, as the screens need?"""
    try:
        done = subprocess.run(["sudo", "-n", "-l", str(PACKAGE_HELPER)], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def needs_system_setup() -> bool:
    from . import nas

    return packaged() and not (helper_allowed() and nas.installed())


def system_setup(library: str, run=None) -> str:
    """The once-only part, run from the terminal (doubletake setup), through sudo: the
    password was asked for already with sudo -v. Returns an error, or ''."""
    if not packaged():
        return "This DoubleTake was installed by setup.sh: run setup.sh again instead."
    run = run or (lambda args: subprocess.run(args, capture_output=True, text=True, timeout=2400))
    try:
        Path(library).mkdir(parents=True, exist_ok=True)
        done = run(["sudo", "-n", str(PACKAGE_HELPER), "adopt", "--library", library])
    except (OSError, subprocess.SubprocessError) as exc:
        return f"The system setup could not start: {exc}"
    if done.returncode != 0 and not (done.stdout or "").strip():
        return "Nothing was changed: sudo did not run the setup. " + (done.stderr or "").strip()[:200]
    try:
        answer = json.loads((done.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return "The system setup did not answer: " + (done.stderr or "").strip()[:200]
    if not answer.get("ok"):
        return answer.get("error") or "The system setup did not finish."
    return ""


def _ours(path: Path, *marks: str) -> bool:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return False
    return any(m in text for m in marks)


def user_setup(home: Path | None = None, run=None) -> list[str]:
    """The user's own part. Safe to run at every start. Returns what was done."""
    if not packaged():
        return []
    home = home or Path.home()
    done = []
    run = run or (lambda args: subprocess.run(args, capture_output=True, text=True, timeout=30))
    # GNOME opens a camera as soon as it is plugged in, which blocks every other program.
    unit = "gvfs-gphoto2-volume-monitor.service"
    try:
        listed = run(["systemctl", "--user", "list-unit-files", unit])
        if unit in (listed.stdout or "") and "masked" not in (listed.stdout or ""):
            run(["systemctl", "--user", "mask", "--now", unit])
            done.append("camera grabber stopped")
    except (OSError, subprocess.SubprocessError):
        pass
    # What an install by setup.sh left behind, now that the package has the program.
    old_command = home / ".local/bin/doubletake"
    if _ours(old_command, "Installed by setup.sh"):
        old_command.unlink()
        done.append("old command removed")
    old_entry = home / ".local/share/applications/doubletake.desktop"
    if _ours(old_entry, str(old_command)):
        old_entry.unlink()
        done.append("old app-list entry removed")
    old_program = home / "DoubleTake"
    if (old_program / "doubletake" / "__init__.py").is_file() and (old_program / "run.py").is_file():
        shutil.rmtree(old_program, ignore_errors=True)
        done.append("old program folder removed")
    if done:
        # Start-up entries written by the old install name its command: point them here.
        from . import kiosk, maintenance, settings

        if settings.AUTOSTART.is_file():
            settings.set_autostart(True)
        if kiosk.SCRIPT.is_file() and kiosk.ours(kiosk.SCRIPT):
            kiosk.write_script()
        if maintenance.AUTOSTART.is_file():
            maintenance.write_autostart()
    return done


def state() -> dict:
    """What the screens show about the system setup."""
    return {"packaged": packaged(), "needed": needs_system_setup() if packaged() else False}
