"""Formatting the microSD card, and putting the tablet back as it came.

Formatting is for the second-copy card only. The camera's cards are formatted in
the camera, as always. Both jobs ask the screens for a clear confirmation first,
and refuse when the result would leave photos with no copy anywhere.
"""

from __future__ import annotations

import getpass
import json
import time
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import cards, config, nas, settings

LABEL_RE = re.compile(r"[^A-Za-z0-9 _-]")


def _card(cfg, ledger):
    found = cards.detect(cfg, ledger)
    card = found.card if found.card is not None else (found.all[0] if len(found.all) == 1 else None)
    if card is None:
        return None, "There is no microSD card in the tablet."
    if not card.device.startswith("/dev/"):
        return None, "The second copy is set to a folder, not a card, so there is nothing to format."
    return card, ""


def _other_files(card, limit=1000) -> int:
    """Files on the card that are not the app's own."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(card.mount):
        if Path(dirpath) == card.mount and card.folder in dirnames:
            dirnames.remove(card.folder)
        dirnames[:] = [d for d in dirnames if not d.startswith((".Trash", "System Volume Information"))]
        count += len([f for f in filenames if not f.startswith("._")])
        if count >= limit:
            break
    return count


def card_report(cfg: dict, ledger) -> dict:
    """What formatting would remove, for the confirmation."""
    card, error = _card(cfg, ledger)
    if card is None:
        return {"error": error}
    held = len(ledger.card_copies(card.card_id)) if card.card_id else 0
    only_here = 0
    if card.card_id:
        rows = ledger.db.execute(
            "SELECT COUNT(*) AS n FROM copies c JOIN files f ON f.id=c.file_id JOIN shoots s ON s.id=f.shoot_id "
            "WHERE c.card_id=? AND c.status='verified' AND s.imported IS NULL", (card.card_id,)).fetchone()
        only_here = rows["n"]
    return {"error": "", "label": card.label, "noun": card.noun, "device": card.device, "total": card.total,
            "free": card.free, "held": held, "unprotected": only_here, "other_files": _other_files(card)}


def format_card(cfg: dict, ledger) -> dict:
    card, error = _card(cfg, ledger)
    if card is None:
        return {"error": error}
    label = LABEL_RE.sub("", card.label or "")[:11].strip() or "FIELDCARD"
    answer = nas.helper("format-card", card.device, label, timeout=900)
    if not answer.get("ok"):
        return {"error": answer.get("error", "The card could not be formatted.")}
    if card.card_id:
        ledger.forget_card(card.card_id)
    # Open it again for the desktop, so it shows up as a new, empty card.
    try:
        subprocess.run(["udisksctl", "mount", "--no-user-interaction", "-b", card.device],
                       capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass
    return {"error": "", "label": label}


DT_LABEL = "DT-COPY2"


def drive_details(cfg: dict, ledger) -> dict:
    """The new drive, whole: its name, size, and the partitions and space used on it now."""
    card, error = _card(cfg, ledger)
    if card is None:
        return {"error": error}
    disk = cards.disk_of(card.device)
    out = {"error": "", "disk": disk, "kind": "card" if card.is_mmc else "usb", "name": "", "size": card.total,
           "used": 0, "partitions": []}
    try:
        done = subprocess.run(["lsblk", "-J", "-b", "-o", "NAME,SIZE,FSTYPE,LABEL,FSUSED,MODEL,VENDOR", disk],
                              capture_output=True, text=True, timeout=20)
        tree = (json.loads(done.stdout or "{}").get("blockdevices") or [{}])[0]
    except (OSError, subprocess.SubprocessError, ValueError):
        tree = {}
    name = " ".join(x for x in ((tree.get("vendor") or "").strip(), (tree.get("model") or "").strip()) if x)
    out["name"] = name or ("microSD card" if card.is_mmc else "USB drive")
    out["size"] = int(tree.get("size") or card.total or 0)
    for child in tree.get("children") or []:
        used = int(child.get("fsused") or 0)
        out["used"] += used
        out["partitions"].append({"label": child.get("label") or "", "fstype": child.get("fstype") or "",
                                  "size": int(child.get("size") or 0), "used": used})
    return out


def prepare_drive(cfg: dict, ledger) -> dict:
    """Erase the whole drive and make it DoubleTake's: one exFAT partition, then use it."""
    card, error = _card(cfg, ledger)
    if card is None:
        return {"error": error}
    if not card.device.startswith("/dev/"):
        return {"error": "The second copy is set to a folder, not a drive."}
    disk = cards.disk_of(card.device)
    answer = nas.helper("prepare-drive", disk, DT_LABEL, timeout=1800)
    if not answer.get("ok"):
        return {"error": answer.get("error", "The drive could not be prepared.")}
    part = answer.get("partition") or ""
    if card.card_id:
        ledger.forget_card(card.card_id)
    # Open it again, then take it for the second copy.
    for _ in range(30):
        try:
            subprocess.run(["udisksctl", "mount", "--no-user-interaction", "-b", part],
                           capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass
        found = cards.detect(cfg, ledger)
        fresh = found.card if found.card is not None and found.card.device == part else None
        if fresh is not None:
            cards.allow(fresh, ledger)
            cards.tag(fresh, ledger)
            ledger.set_card(f"id:{fresh.card_id}", "used", fresh.label)
            return {"error": "", "label": DT_LABEL}
        time.sleep(1)
    return {"error": "The drive was prepared, but did not open again. Take it out and put it back."}


def reset_settings(cfg: dict) -> dict:
    """Settings back to how they came. Photos, records and the cards are left alone."""
    from . import poweron

    if cfg.get("desktop_saved"):
        poweron.switch(cfg, False, getpass.getuser())  # put the desktop's lock and blanking back
    user = config.read_user()
    keep = {k: user[k] for k in config.PATH_KEYS if k in user}  # never lose track of where the photos are
    path = config.config_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    if keep:
        config.save(keep)
    settings.set_autostart(False)
    for key, value in config.DEFAULTS.items():
        if key not in config.PATH_KEYS:
            cfg[key] = value
    problem = ""
    if nas.installed():
        answer = nas.helper("reset")
        problem = "" if answer.get("ok") else answer.get("error", "")
        nas.sync_zones(cfg)
    return {"error": problem}


def erase_everything(cfg: dict, library: Path, data_dir: Path, confirmed: bool) -> dict:
    """Remove every photo and record on the tablet, and the settings.

    Refused, unless confirmed by typing, while any shoot is not yet imported.
    The microSD card and the camera's cards are not touched.
    """
    from .ledger import Ledger

    library, data_dir = Path(library), Path(data_dir)
    home = Path(os.path.expanduser("~")).resolve()
    for folder in (library, data_dir):
        real = folder.resolve()
        if real in (home, Path("/")) or home not in real.parents:
            return {"error": f"{folder} is not a folder this app may erase."}
    ledger = Ledger(library, data_dir)
    try:
        unimported = ledger.unimported_shoots()
    finally:
        ledger.close()
    if unimported and not confirmed:
        return {"error": "", "needs_confirm": True, "unimported": unimported}
    for folder in (library, data_dir):
        shutil.rmtree(folder, ignore_errors=True)
    library.mkdir(parents=True, exist_ok=True)
    result = reset_settings(cfg)
    return {"error": result.get("error", ""), "erased": True}
