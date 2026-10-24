"""Naming photos from a pattern, as they are imported or afterwards for a whole shoot.

The camera's own name is always kept in the records, so a photo still on the camera
is recognised however it has been renamed, and is never copied twice. Nothing on
the camera is ever renamed.

A RAW file and a JPEG shot together (DSC_4841.NEF and DSC_4841.JPG) get the same new
name, with their own extensions.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

DEFAULT_PATTERN = "{date}_{shoot}_{seq}"
TOKENS = {
    "{date}": "the date it was taken: 2026-10-11",
    "{time}": "the time it was taken: 141205",
    "{shoot}": "the shoot name",
    "{seq}": "a running number in the shoot: 0001",
    "{orig}": "the camera's own name: DSC_4841",
    "{num}": "the camera's number: 4841",
    "{camera}": "the camera: D850",
}
# One of these makes every name in a shoot different.
UNIQUE = ("{seq}", "{orig}", "{num}")
_BAD = re.compile(r"[^A-Za-z0-9._+-]+")


def check(pattern: str) -> str:
    """'' when the pattern can be used, or why not."""
    pattern = (pattern or "").strip()
    if not pattern:
        return "The pattern is empty."
    if len(pattern) > 80:
        return "The pattern is too long."
    unknown = [t for t in re.findall(r"\{[^}]*\}", pattern) if t not in TOKENS]
    if unknown:
        return "Unknown part: " + ", ".join(unknown)
    if not any(t in pattern for t in UNIQUE):
        return "Include {seq}, {orig} or {num}, so every photo gets a different name."
    if "/" in pattern or "\\" in pattern:
        return "A name cannot contain a slash."
    return ""


def _clean(text: str) -> str:
    text = _BAD.sub("-", text.strip()).strip("-.")
    return re.sub(r"-{2,}", "-", text)


def camera_short(model: str) -> str:
    """'Nikon D850' -> 'D850', 'Sony α7R V' -> 'a7R-V'."""
    model = (model or "").replace("α", "a")
    parts = model.split(" ", 1)
    return _clean(parts[1] if len(parts) == 2 else model)


def stem_of(name: str) -> str:
    return os.path.splitext(name)[0]


def make(pattern: str, *, name: str, taken: float, shoot: str, seq: int, camera: str = "") -> str:
    """The new file name (with the original extension) for one photo."""
    stem = stem_of(name)
    ext = os.path.splitext(name)[1]
    t = time.localtime(taken)
    digits = (re.findall(r"\d+", stem) or [""])[-1]
    parts = {
        "{date}": time.strftime("%Y-%m-%d", t),
        "{time}": time.strftime("%H%M%S", t),
        "{shoot}": _clean(shoot) or "shoot",
        "{seq}": f"{seq:04d}",
        "{orig}": _clean(stem),
        "{num}": digits or _clean(stem),
        "{camera}": camera_short(camera) or "camera",
    }
    out = pattern
    for token, value in parts.items():
        out = out.replace(token, value)
    out = _clean(out) or _clean(stem)
    return out[:120] + ext


def plan_names(files: list, pattern: str, shoot: str, camera: str = "", start: int = 1, taken_names: set = None) -> dict:
    """New names for [(key, original name, capture time)], in capture order.

    Files with the same original stem (a RAW and its JPEG) share a number and a name.
    Names in 'taken_names' are avoided by adding _2, _3. Returns {key: new name}.
    """
    taken_names = {n.lower() for n in (taken_names or set())}
    order = sorted(files, key=lambda x: (x[2], x[1]))
    seq_of, out, seq = {}, {}, start
    for key, name, when in order:
        stem = stem_of(name).lower()
        if stem not in seq_of:
            seq_of[stem] = (seq, when)
            seq += 1
        number, first_time = seq_of[stem]
        new = make(pattern, name=name, taken=first_time, shoot=shoot, seq=number, camera=camera)
        base, ext = os.path.splitext(new)
        n = 2
        while new.lower() in taken_names:
            new = f"{base}_{n}{ext}"
            n += 1
        taken_names.add(new.lower())
        out[key] = new
    return out


def example(pattern: str, shoot: str = "Ramp-sunset", camera: str = "Nikon D850") -> str:
    when = time.mktime((2026, 10, 11, 14, 12, 5, 0, 0, -1))
    return make(pattern, name="DSC_4841.NEF", taken=when, shoot=shoot, seq=1, camera=camera)


# ----- renaming a shoot that is already on the tablet --------------------------------------


def rename_files(folder: Path, moves: list) -> list:
    """Rename [(old, new)] inside one folder, in two steps so names can swap safely.

    Returns the moves that were made. Stops at the first problem, putting back what it
    moved, so a folder is never left half renamed.
    """
    moves = [(o, n) for o, n in moves if o != n]
    staged = []
    try:
        for i, (old, new) in enumerate(moves):
            tmp = folder / f".renaming-{i}-{os.getpid()}"
            os.rename(folder / old, tmp)
            staged.append((old, new, tmp))
        for old, new, tmp in staged:
            if (folder / new).exists():
                raise FileExistsError(f"{new} is already there")
        done = []
        for old, new, tmp in staged:
            os.rename(tmp, folder / new)
            done.append((old, new))
        return done
    except OSError:
        for old, new, tmp in reversed(staged):
            try:
                if tmp.exists():
                    os.rename(tmp, folder / old)
                elif (folder / new).exists() and not (folder / old).exists():
                    os.rename(folder / new, folder / old)
            except OSError:
                pass
        raise


def preview_shoot(ledger, shoot_id: int, pattern: str) -> dict:
    """What renaming a shoot would do: [(old, new)] and whether it may be done."""
    shoot = ledger.shoot(shoot_id)
    if shoot is None:
        return {"error": "That shoot is not in the records."}
    if shoot["cleared"] is not None:
        return {"error": "That shoot has been cleared from the tablet."}
    if shoot["imported"] is not None:
        return {"error": "This shoot is marked as imported. Renaming it now would make your photo app "
                         "lose track of the photos, so it is not renamed."}
    problem = check(pattern)
    if problem:
        return {"error": problem}
    rows = [r for r in ledger.shoot_files(shoot_id) if r["status"] == "verified"]
    names = plan_names([(r["id"], r["src_name"], r["mtime"]) for r in rows], pattern, shoot["name"],
                       shoot["source_model"] or "")
    moves = [(r["id"], r["rel_name"] or r["src_name"], names[r["id"]]) for r in rows]
    return {"moves": moves, "changes": sum(1 for _, old, new in moves if old != new), "folder": shoot["folder"]}


def rename_shoot(ledger, library: Path, shoot_id: int, pattern: str, card=None) -> dict:
    """Rename a shoot's photos on the tablet, and on the second-copy card.

    The card in the tablet is renamed now; a card that is out is renamed the next
    time it is put in. Returns {'renamed': n} or {'error': ...}.
    """
    from .manifest import write_manifest

    plan = preview_shoot(ledger, shoot_id, pattern)
    if plan.get("error"):
        return plan
    moves = [(fid, old, new) for fid, old, new in plan["moves"] if old != new]
    if not moves:
        return {"renamed": 0}
    folder = Path(library) / plan["folder"]
    # Files in the folder that the app did not put there keep their names, and are not overwritten.
    ours = {old.lower() for _, old, _ in plan["moves"]}
    strangers = {p.name.lower() for p in folder.iterdir() if p.name.lower() not in ours}
    clash = [new for _, _, new in moves if new.lower() in strangers]
    if clash:
        return {"error": f"{clash[0]} is already in the shoot folder and is not one of its photos."}
    rename_files(folder, [(old, new) for _, old, new in moves])
    for fid, old, new in moves:
        ledger.set_rel_name(fid, new, commit=False)
        for card_id in ledger.card_ids_of_file(fid):
            ledger.add_pending_rename(fid, card_id, old)
    ledger.db.commit()
    write_manifest(ledger, shoot_id)
    on_card = apply_pending(ledger, card, library) if card is not None and card.card_id else 0
    return {"renamed": len(moves), "card_renamed": on_card}


def apply_pending(ledger, card, library) -> int:
    """Rename the copies on this card that were renamed on the tablet while it was out."""
    from .manifest import write_manifest
    from .mirror import _copy_small

    rows = ledger.pending_renames(card.card_id)
    if not rows:
        return 0
    done, shoots = 0, {}
    for r in rows:
        shoots.setdefault((r["shoot_id"], r["shoot_folder"]), []).append(r)
    for (shoot_id, shoot_folder), items in shoots.items():
        folder = card.root / shoot_folder
        moves = [(r["old_rel"], r["rel_name"] or r["src_name"]) for r in items
                 if (folder / r["old_rel"]).exists() and r["old_rel"] != (r["rel_name"] or r["src_name"])]
        try:
            if moves:
                rename_files(folder, moves)
                done += len(moves)
        except OSError:
            continue  # tried again the next time the card is seen
        for r in items:
            ledger.drop_pending_rename(r["file_id"], card.card_id)
        try:
            _copy_small(write_manifest(ledger, shoot_id), folder / "manifest.json")
        except OSError:
            pass
    return done
