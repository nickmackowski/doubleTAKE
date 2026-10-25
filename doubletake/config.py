"""Settings. One JSON file, with defaults for anything it does not mention."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS = {
    # Where shoots are stored on the tablet. Empty means Pictures/DoubleTake.
    "library": "",
    # Where the records are kept: the database, the run logs and the thumbnails.
    # Empty means Documents/DoubleTake. Kept apart from the program, so that an
    # upgrade never touches the photos or the records.
    "data_dir": "",
    # How to pull files from the camera: "auto", "chunked" or "whole".
    # "auto" uses chunked reads when the camera supports them.
    "download_method": "auto",
    # Size of each chunked read, in megabytes.
    "chunk_mb": 8,
    # The second copy: "auto" uses a card found in the tablet, "off" switches it
    # off, and a folder path uses that folder.
    "second_copy": "auto",
    # The app's own folder on the card. Nothing is written outside it.
    "second_copy_folder": "DoubleTake",
    # The screens are served on this port, to this tablet only.
    "port": 8765,
    # Shoot names offered as one-tap buttons when a backup starts.
    "quick_names": [],  # the user adds their own
    # The look of the screens: "daylight", "slate", "night" or "nightred".
    "theme": "daylight",
    "screen_dark_after": 5,       # minutes with no touch before the screen goes dark; 0 never
    "sleep_on_battery": 30,       # minutes with no touch before the tablet sleeps on battery; 0 never
    "auto_backup": False,         # back up as soon as the camera is plugged in
    "auto_dark": True,            # darken the screen between unattended backups
    "auto_chime": True,           # a short chime when an unattended backup is done
    "copy_what": "raw_jpeg",     # raw, raw_jpeg, or all (videos too)
    "look": "classic",            # classic, or cockpit
    "cockpit_theme": "cockpit",   # cockpit (dark), or cockpit-light
    # After a shoot is marked as imported: "keep" its NEFs for
    # keep_days, then offer to clear them, or "delete" them straight away.
    "after_import": "keep",
    "keep_days": 30,
    # The tablet's own Wi-Fi network, for a laptop to join. The password is made
    # the first time the hotspot is switched on.
    "hotspot_name": "DoubleTake",
    "hotspot_password": "",
    # Where the NAS may be seen: always on the cable and the hotspot unless switched
    # off, and on these Wi-Fi networks (by name, password-protected ones only).
    "nas_cable": True,
    "nas_hotspot": True,
    "nas_trusted": [],
    # A cable that finds no network becomes a direct link to the laptop at the other end.
    "direct_cable": True,
    # Where the second copy lives: "microsd" (the card slot) or "usb" (a USB drive).
    "second_copy_device": "microsd",
    # Rename photos as they come off the camera, from a pattern (see naming.py).
    "rename_on_import": False,
    "rename_pattern": "{date}_{shoot}_{seq}",
    # How a shoot's photos are ordered: by capture time or name, either way round.
    "photo_sort": "taken_asc",
    # The zoom level double-tap goes to in the viewer: 50, 75 or 100 percent.
    "viewer_zoom": 100,
    # The viewer's line of photo details on the photo: shown, or hidden with a tap.
    "viewer_info": True,
    # The desktop's own lock and blanking settings, kept while Start at power-on is on.
    "desktop_saved": {},
}


PATH_KEYS = ("library", "data_dir")
OLD_LIBRARY = "~/FieldPhotos"  # where versions before 0.4.7 kept everything


def user_dir(kind: str, fallback: str) -> Path:
    """The desktop's own folder for pictures or documents, as set in user-dirs.dirs."""
    home = os.path.expanduser("~")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    try:
        with open(os.path.join(base, "user-dirs.dirs"), encoding="utf-8") as fh:
            for row in fh:
                if row.startswith(f"XDG_{kind}_DIR="):
                    value = row.split("=", 1)[1].strip().strip('"').replace("$HOME", home)
                    if value and value != home:
                        return Path(value)
    except OSError:
        pass
    return Path(os.path.expanduser(fallback))


def default_library() -> Path:
    return user_dir("PICTURES", "~/Pictures") / "DoubleTake"


RECORDS = ".doubletake"  # the records folder kept inside a library of the user's choosing


def default_data_dir() -> Path:
    return user_dir("DOCUMENTS", "~/Documents") / "DoubleTake"


def data_dir(cfg: dict) -> Path:
    """Where the records are, for a loaded or hand-made set of settings."""
    if cfg.get("data_dir"):
        return Path(cfg["data_dir"])
    return Path(cfg["library"]) / RECORDS


def read_user() -> dict:
    """Only what the settings file itself says."""
    path = config_path()
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            user = json.load(fh)
    except (OSError, ValueError):
        return {}
    return user if isinstance(user, dict) else {}


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "doubletake" / "config.json"


def load() -> dict:
    cfg = dict(DEFAULTS)
    path = config_path()
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                user = json.load(fh)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"Could not read settings file {path}: {exc}")
        if not isinstance(user, dict):
            raise SystemExit(f"Settings file {path} must contain a JSON object.")
        cfg.update(user)
    if cfg.get("second_copy_folder") == "FieldPhotos":
        cfg["second_copy_folder"] = "DoubleTake"  # its name before 0.9.5; the card is renamed when seen
    # An environment variable wins, which keeps tests away from the real library.
    custom = bool(cfg.get("library"))
    if os.environ.get("DOUBLETAKE_LIBRARY"):
        cfg["library"], custom = os.environ["DOUBLETAKE_LIBRARY"], True
    library = Path(os.path.expanduser(cfg["library"])) if cfg.get("library") else default_library()
    cfg["library"] = str(library.resolve())
    if os.environ.get("DOUBLETAKE_DATA"):
        cfg["data_dir"] = os.environ["DOUBLETAKE_DATA"]
    if cfg.get("data_dir"):
        data = Path(os.path.expanduser(cfg["data_dir"]))
    elif custom:
        # A library somewhere of the user's own choosing keeps its records inside it, as before.
        data = library / RECORDS
        old = library / ".fieldstation"  # its name before 0.9.5
        if old.is_dir() and not data.exists():
            try:
                old.rename(data)
            except OSError:
                data = old
    else:
        data = default_data_dir()
    cfg["data_dir"] = str(data.resolve())
    return cfg


def write_defaults_if_missing() -> Path:
    path = config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({k: v for k, v in DEFAULTS.items() if k not in PATH_KEYS}, fh, indent=2)
            fh.write("\n")
    return path


def save(changes: dict) -> Path:
    """Write some settings to the settings file, keeping everything else in it."""
    path = config_path()
    current = {}
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                current = json.load(fh)
        except (OSError, ValueError):
            current = {}
        if not isinstance(current, dict):
            current = {}
    current.update(changes)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".config.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(current, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path
