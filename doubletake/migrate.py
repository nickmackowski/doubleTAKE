"""Moving photos and records from the single folder used before 0.4.7.

Before 0.4.7 everything lived in ~/FieldPhotos, with the records in a hidden
.fieldstation folder inside it. Now the photos live in Pictures/DoubleTake
and the records in Documents/DoubleTake, so that installing a new version
never touches either. Moving is a rename on the same drive: instant, and
nothing is copied or rewritten. The records store shoot folders relative to the
library, so they stay valid after the move.
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path

from . import config


class MoveError(Exception):
    pass


@dataclass
class Plan:
    state: str  # "none", "move" or "blocked"
    message: str
    old: Path
    new_library: Path
    new_data: Path


def _has_content(path: Path) -> bool:
    try:
        return any(path.iterdir())
    except OSError:
        return False


def plan() -> Plan:
    old = Path(os.path.expanduser(config.OLD_LIBRARY))
    new_library, new_data = config.default_library(), config.default_data_dir()
    user = config.read_user()
    chosen = user.get("library")
    # Someone who chose a library of their own keeps it. Only the old default moves.
    if chosen and Path(os.path.expanduser(chosen)) != old:
        return Plan("none", "The photos are in a folder of your own choosing. Nothing to move.", old,
                    new_library, new_data)
    if chosen and user.get("data_dir"):
        # Kept where they were when setup asked before: do not ask again.
        return Plan("none", f"Photos and records are kept in {old}, as chosen before.", old, new_library, new_data)
    if not old.is_dir() or not _has_content(old):
        return Plan("none", "Nothing from an earlier version to move.", old, new_library, new_data)
    for target in (new_library, new_data):
        if _has_content(target):
            return Plan("blocked", f"Both {old} and {target} have things in them, so nothing was moved. "
                                   "Move or remove one of them, then run doubletake setup in a terminal.", old, new_library, new_data)
    return Plan("move", f"Found photos and records from an earlier version in {old}.\n"
                        f"  Photos would move to   {new_library}\n"
                        f"  Records would move to  {new_data}", old, new_library, new_data)


def move(p: Plan) -> str:
    try:
        p.new_library.parent.mkdir(parents=True, exist_ok=True)
        if p.new_library.is_dir():
            p.new_library.rmdir()  # empty, checked in plan()
        os.rename(p.old, p.new_library)
        records = p.new_library / ".fieldstation"
        if records.is_dir():
            p.new_data.parent.mkdir(parents=True, exist_ok=True)
            if p.new_data.is_dir():
                p.new_data.rmdir()
            os.rename(records, p.new_data)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            raise MoveError("the new folders are on a different drive, which would mean copying "
                            "everything. Nothing was changed.") from exc
        raise MoveError(str(exc)) from exc
    # The settings file from earlier versions named the old folder; let the new defaults apply.
    user = config.read_user()
    if any(k in user for k in config.PATH_KEYS):
        for key in config.PATH_KEYS:
            user.pop(key, None)
        _write_user(user)
    return f"Moved. Photos: {p.new_library}   Records: {p.new_data}"


def keep_old() -> None:
    """Keep using ~/FieldPhotos as before, records and all."""
    old = os.path.expanduser(config.OLD_LIBRARY)
    config.save({"library": old, "data_dir": os.path.join(old, ".fieldstation")})


def _write_user(user: dict) -> None:
    import json

    path = config.config_path()
    tmp = path.with_name(".config.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(user, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
