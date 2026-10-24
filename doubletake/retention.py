"""Deleting shoots from Copy 1 (the tablet) and Copy 2.

DoubleTake's job is the two copies; keeping the photos safe elsewhere, on the editing
workstation, is the photographer's. So any shoot may be deleted, with one parachute: not
within a day of its last photo being copied (HOLD). Only the files this app wrote are
removed (the photos it recorded, and its report); anything else found in a shoot's folder
is left where it is. The camera's cards are never touched. The records are kept, so the
same photos are never copied again from a camera card that was not formatted.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import editor, config, photos

DAY = 86400
HOLD = DAY  # a shoot can be deleted only this long after its last photo was copied


def last_copied(ledger, shoot_id: int) -> float:
    row = ledger.db.execute("SELECT MAX(copied) AS t FROM files WHERE shoot_id=?", (shoot_id,)).fetchone()
    return float(row["t"] or 0) if row else 0.0


def deletable_from(ledger, shoot_id: int) -> float:
    """When the shoot may be deleted: a day after its last photo was copied."""
    return last_copied(ledger, shoot_id) + HOLD


def _remove(path: Path) -> int:
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except FileNotFoundError:
        return 0


def _prune(folder: Path, stop: Path) -> None:
    """Remove empty folders from `folder` up to, not including, `stop`."""
    for leftover in (folder / ".incoming",):
        try:
            leftover.rmdir()
        except OSError:
            pass
    while folder != stop and stop in folder.parents:
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def clear_shoot(ledger, cfg: dict, shoot_id: int, card=None, now: float | None = None) -> dict:
    """Delete one shoot's photos from the tablet, and from Copy 2 when it is in.

    Returns what was done. Raises ValueError for a shoot that may not be deleted yet.
    """
    shoot = ledger.shoot(shoot_id)
    if shoot is None:
        raise ValueError("No such shoot.")
    if shoot["cleared"] is not None:
        raise ValueError(f"{shoot['name'] or shoot['folder']} is already deleted.")
    now = time.time() if now is None else now
    if now < deletable_from(ledger, shoot_id):
        raise ValueError(f"{shoot['folder'].rsplit('/', 1)[-1]} was copied less than a day ago, so it is kept a while "
                         "longer, in case something went wrong.")
    library = Path(cfg["library"])
    folder = library / shoot["folder"]
    removed = freed = 0
    for f in ledger.shoot_files(shoot_id):
        if f["status"] != "verified":
            continue
        name = f["rel_name"] or f["src_name"]
        got = _remove(folder / name)
        if got:
            removed += 1
            freed += got
        photos.forget(config.data_dir(cfg) / "previews" / str(f["id"]))
        editor.forget(config.data_dir(cfg), f["id"])  # its edits go too; exports are kept
    _remove(folder / "manifest.json")
    _prune(folder, library)
    ledger.set_cleared(shoot_id, time.time())
    on_card = clear_on_card(ledger, card, shoot) if card is not None else 0
    return {"removed": removed, "freed": freed, "card_removed": on_card}


def clear_on_card(ledger, card, shoot) -> int:
    """Remove a cleared shoot's second copy from the card. Returns files removed."""
    folder = card.root / shoot["folder"]
    removed = 0
    for f in ledger.copies_of_shoot(shoot["id"], card.card_id):
        name = f["rel_name"] or f["src_name"]
        if _remove(folder / name):
            removed += 1
        ledger.drop_copy(f["id"], card.card_id, commit=False)
    ledger.commit()
    _remove(folder / "manifest.json")
    _prune(folder, card.root)
    return removed


def card_cleanup(ledger, card) -> int:
    """Finish clearing shoots whose second copy was on a card that was out at the time."""
    total = 0
    for shoot in ledger.cleared_with_copies(card.card_id):
        total += clear_on_card(ledger, card, shoot)
    return total
