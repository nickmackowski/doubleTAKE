"""The second copy: mirror verified shoots from the tablet to a card.

Each file is written to the card, read back from the card, and compared with
the checksum recorded when it came off the camera. Only then is it counted.

Nothing on the card outside the app's own folder is touched, and nothing on
the card is ever erased to make room. (Preparing a card, which erases it, is
separate and always asked for: see device.prepare_drive.)
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from .cards import Card
from .manifest import write_manifest
from .util import STOPPED, StopRequested

_BLOCK = 4 * 1024 * 1024
# Leave this much free on the card.
CARD_MARGIN = 200 * 1000**2
FULL = "The second copy's card is full."


@dataclass
class MirrorResult:
    card: Card
    wanted: int = 0  # files this run set out to copy
    wanted_bytes: int = 0
    verified: int = 0
    failed: int = 0
    bytes_done: int = 0
    seconds: float = 0.0
    write_seconds: float = 0.0
    write_bytes: int = 0
    read_seconds: float = 0.0
    read_bytes: int = 0
    interrupted: bool = False
    reason: str = ""
    failures: list = field(default_factory=list)  # (name, note)
    dropped: int = 0  # records removed because the file is no longer on the card
    left: int = 0  # files still waiting after this run
    left_bytes: int = 0


def quick_check(ledger, card: Card) -> int:
    """Make sure files recorded as on the card are still there, by name and size.

    A file that has gone is forgotten, so it is copied again. Returns how many.
    """
    dropped = 0
    for row in ledger.card_copies(card.card_id):
        path = card.root / row["shoot_folder"] / (row["rel_name"] or row["src_name"])
        try:
            ok = path.stat().st_size == row["size"]
        except OSError:
            ok = False
        if not ok:
            ledger.drop_copy(row["id"], card.card_id, commit=False)
            dropped += 1
    ledger.commit()
    return dropped


def waiting(ledger, card: Card):
    """(files, bytes) not yet on this card."""
    rows = ledger.pending_copies(card.card_id)
    return len(rows), sum(r["size"] for r in rows)


def run_mirror(cfg: dict, ledger, card: Card, reporter=None, limit: int | None = None) -> MirrorResult:
    """Copy everything the card does not hold yet. Safe to interrupt and run again."""
    library = Path(cfg["library"])
    result = MirrorResult(card=card)
    ledger.set_card(f"id:{card.card_id}", "used", card.label)
    result.dropped = quick_check(ledger, card)

    rows = ledger.pending_copies(card.card_id)
    if limit is not None:
        rows = rows[: max(0, limit)]
    result.wanted = len(rows)
    result.wanted_bytes = sum(r["size"] for r in rows)
    if not rows:
        result.left, result.left_bytes = waiting(ledger, card)
        return result

    if reporter:
        reporter.mirror_start(result.wanted, result.wanted_bytes, card)
    on_bytes = reporter.bytes if reporter else (lambda n: None)
    touched: dict[int, str] = {}
    started = time.time()
    try:
        for index, row in enumerate(rows, start=1):
            name = row["rel_name"] or row["src_name"]
            item = SimpleNamespace(name=name, size=row["size"])
            if reporter:
                reporter.file_start(index, item)
            if shutil.disk_usage(card.mount).free < row["size"] + CARD_MARGIN:
                result.interrupted = True
                result.reason = FULL
                break
            src = library / row["shoot_folder"] / name
            dst = card.root / row["shoot_folder"] / name
            status, note = _mirror_one(src, dst, row["sha256"], row["size"], row["mtime"], on_bytes, result)
            if status == "verified":
                ledger.set_copy(row["id"], card.card_id, "verified", note)
                result.verified += 1
                result.bytes_done += row["size"]
                touched[row["shoot_id"]] = row["shoot_folder"]
            else:
                result.failed += 1
                result.failures.append((name, note))
            if reporter:
                reporter.file_done(item, status, note)
    except (KeyboardInterrupt, StopRequested):
        result.interrupted, result.reason = True, STOPPED
    except OSError as exc:
        result.interrupted = True
        if exc.errno == errno.ENOSPC:
            result.reason = FULL
        elif exc.errno in (errno.EIO, errno.ENOENT, errno.ENODEV, errno.ENXIO, errno.EROFS, errno.ENOTCONN):
            result.reason = "The card was removed or stopped responding."
        else:
            result.reason = f"Could not finish copying to the card: {exc}"
    result.seconds = time.time() - started

    # Refresh each shoot's report, and put a copy of it beside the photos on the card.
    for shoot_id, folder in touched.items():
        try:
            report = write_manifest(ledger, shoot_id)
            _copy_small(report, card.root / folder / "manifest.json")
        except OSError:
            pass
    card.refresh()
    result.left, result.left_bytes = waiting(ledger, card)
    return result


def _mirror_one(src: Path, dst: Path, sha: str, size: int, mtime: int, on_bytes, result: MirrorResult):
    """Copy one file to the card and prove the card holds it. Returns (status, note)."""
    if not src.is_file():
        return "failed", "missing on the tablet"
    dst.parent.mkdir(parents=True, exist_ok=True)

    if dst.is_file() and dst.stat().st_size == size:
        # Possibly here already, from a run that stopped before recording it.
        if _read_back(dst, on_bytes, 1.0, result) == sha:
            return "verified", "already on the card"
        on_bytes(-size)

    part = dst.parent / f".{dst.name}.part"
    h = hashlib.sha256()
    t0 = time.time()
    with open(src, "rb") as fin, open(part, "wb") as out:
        while True:
            block = fin.read(_BLOCK)
            if not block:
                break
            out.write(block)
            h.update(block)
            on_bytes(len(block) / 2)
        out.flush()
        os.fsync(out.fileno())
        _forget_cached(out.fileno())
    result.write_seconds += time.time() - t0
    result.write_bytes += size

    if h.hexdigest() != sha:
        part.unlink()
        return "failed", "the copy on the tablet no longer matches its checksum"

    # Read it back from the card before it is given its real name.
    if _read_back(part, on_bytes, 0.5, result) != sha:
        part.unlink()
        return "failed", "the card gave back different data than was written to it"

    os.replace(part, dst)
    _fsync_dir(dst.parent)
    try:
        os.utime(dst, (mtime, mtime))
    except OSError:
        pass
    return "verified", ""


def _read_back(path: Path, on_bytes, weight: float, result: MirrorResult) -> str:
    h = hashlib.sha256()
    t0 = time.time()
    done = 0
    with open(path, "rb") as fh:
        _forget_cached(fh.fileno())
        while True:
            block = fh.read(_BLOCK)
            if not block:
                break
            h.update(block)
            done += len(block)
            on_bytes(len(block) * weight)
    result.read_seconds += time.time() - t0
    result.read_bytes += done
    return h.hexdigest()


def verify_card(ledger, card: Card, shoot, on_file=None):
    """Re-read a shoot's files from the card and compare them with their checksums.

    Returns (matching, problems, not_on_card). A file that is missing or changed
    is forgotten, so the next mirror copies it again.
    """
    held = {r["id"]: r for r in ledger.card_copies(card.card_id, shoot["id"])}
    files = [f for f in ledger.shoot_files(shoot["id"]) if f["status"] == "verified"]
    ok, problems = 0, []
    scratch = MirrorResult(card=card)
    for f in files:
        if f["id"] not in held:
            continue
        path = card.root / shoot["folder"] / (f["rel_name"] or f["src_name"])
        if not path.is_file():
            problems.append(("MISSING", path))
        elif _read_back(path, lambda n: None, 1.0, scratch) != f["sha256"]:
            problems.append(("CHANGED", path))
        else:
            ok += 1
            if on_file:
                on_file(f)
            continue
        ledger.drop_copy(f["id"], card.card_id)
    return ok, problems, len(files) - len(held)


# ----- helpers ----------------------------------------------------------------


def _forget_cached(fd: int) -> None:
    """Ask the system to drop its in-memory copy of a file, so that reading it
    back really comes from the card."""
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except (AttributeError, OSError):
        pass


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _copy_small(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / f".{dst.name}.tmp"
    with open(src, "rb") as fin, open(tmp, "wb") as out:
        out.write(fin.read())
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, dst)
