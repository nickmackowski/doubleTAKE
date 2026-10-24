"""Moving over from the name Field Station, used before 0.8.1.

DoubleTake was called Field Station. setup.sh moves the settings and the program's own
folder (~/.config/fieldstation, ~/.local/share/fieldstation) before anything else, and the
root helper moves what root owns. This moves the rest, once, and is safe to run again:

- the photos and records, from Pictures/FieldStation and Documents/FieldStation, when they
  are in the default places (a folder of your own choosing is left where it is). A move is
  a rename on the same drive: instant, nothing is copied, and the records keep working,
  because they name shoots relative to the photos folder;
- the hotspot's name, when it is still the old default;
- the desktop's entries: start at power-on, a maintenance visit under way, the app list
  and the dock;
- the kiosk script, which starts DoubleTake instead;
- the network connections DoubleTake makes for itself, which it makes again when needed.

The second-copy card keeps its folder (FieldPhotos) and its mark (.fieldstation/card.json),
so every card is still recognised.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import config

OLD = "FieldStation"
OLD_DESKTOP = ("fieldstation.desktop", "fieldstation-restart.desktop")
OLD_CONNECTIONS = ("FieldStation-Direct", "FieldStation-Hotspot")


def _has_content(path: Path) -> bool:
    try:
        return any(path.iterdir())
    except OSError:
        return False


def _remove(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except OSError:
        return False


def move_folders(user: dict, notes: list) -> bool:
    """The photos and the records, from the old default folders. True when the settings changed."""
    changed = False
    pairs = (("library", config.user_dir("PICTURES", "~/Pictures") / OLD, config.default_library(), "Photos"),
             ("data_dir", config.user_dir("DOCUMENTS", "~/Documents") / OLD, config.default_data_dir(), "Records"))
    for key, old, new, what in pairs:
        chosen = user.get(key)
        if chosen and Path(os.path.expanduser(chosen)).resolve() != old.resolve():
            continue  # a folder of their own choosing
        if old.is_dir() and _has_content(old):
            if _has_content(new):
                notes.append(f"NOT MOVED: both {old} and {new} have things in them. "
                             "Move or remove one of them, then run doubletake setup in a terminal.")
                continue
            try:
                new.parent.mkdir(parents=True, exist_ok=True)
                if new.is_dir():
                    new.rmdir()
                os.rename(old, new)
            except OSError as exc:
                notes.append(f"NOT MOVED: {old}: {exc}")
                continue
            notes.append(f"{what} moved: {old} -> {new}")
        elif old.is_dir():
            try:
                old.rmdir()  # empty
            except OSError:
                pass
        if chosen:
            user.pop(key)  # it named the old default; the new default applies
            changed = True
    return changed


def _write_user(user: dict) -> None:
    from .migrate import _write_user as write

    write(user)


def desktop(notes: list) -> None:
    from . import kiosk, maintenance, settings

    autostart = Path(os.path.expanduser("~/.config/autostart"))
    if _remove(autostart / "fieldstation.desktop"):
        settings.set_autostart(True)
        notes.append("Start at power-on: moved over")
    if _remove(autostart / "fieldstation-maintenance.desktop") and maintenance.active():
        maintenance.write_autostart()
    apps = Path(os.path.expanduser("~/.local/share/applications"))
    for name in OLD_DESKTOP:
        _remove(apps / name)
    favorites = maintenance._favorites()
    swapped = [{"fieldstation.desktop": "doubletake.desktop",
                "fieldstation-restart.desktop": maintenance.RESTART_APP}.get(a, a) for a in favorites]
    if swapped != favorites:
        maintenance._set_favorites(swapped)
    if maintenance.active():
        # A visit under way: its wallpaper and dock button moved with the program's folder.
        data = maintenance._load()
        if data.get("state") == "visiting":
            maintenance._save(maintenance.dress_desktop(data))
    # The kiosk script starts DoubleTake now, and someone else's kept script keeps its place.
    before_old = kiosk.SCRIPT.with_name(kiosk.SCRIPT.name + ".before-fieldstation")
    before_new = kiosk.SCRIPT.with_name(kiosk.SCRIPT.name + ".before-doubletake")
    if before_old.is_file() and not before_new.exists():
        before_old.replace(before_new)
    if kiosk.SCRIPT.is_file() and kiosk.ours(kiosk.SCRIPT):
        if not kiosk.write_script(kiosk.SCRIPT):
            notes.append("DoubleTake only: the kiosk now starts DoubleTake")
    _remove(Path(os.path.expanduser("~/.local/bin/fieldstation")))


def connections(notes: list, run=subprocess.run) -> None:
    if not shutil.which("nmcli"):
        return
    for name in OLD_CONNECTIONS:
        try:
            done = run(["nmcli", "connection", "delete", "id", name], capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            notes.append(f"Network connection {name} removed; DoubleTake makes its own again when needed")


def run() -> list:
    notes: list = []
    user = config.read_user()
    changed = move_folders(user, notes)
    if user.get("hotspot_name") == OLD:
        user["hotspot_name"] = "DoubleTake"
        changed = True
        notes.append("Hotspot name: DoubleTake")
    if changed and config.config_path().is_file():
        _write_user(user)
    desktop(notes)
    connections(notes)
    return notes
