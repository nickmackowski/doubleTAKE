"""The backup itself: work out what is new, copy it, check it, record it."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import checks, naming
from .manifest import write_manifest
from .sources import NotSupported, Source, SourceError, SourceFile, SourceInfo
from .util import STOPPED, StopRequested, local_date

# Keep this much free on the tablet after a backup.
SPACE_MARGIN = 2 * 1000**3

# Two files with the same name and size, timed this close together, are the
# same shot written to both card slots.
SAME_SHOT_SECONDS = 10


class BackupError(Exception):
    """A backup could not start, with a message fit to show the user."""


# ----- what to copy ---------------------------------------------------------------

RAW = {"nef", "nrw", "cr2", "cr3", "crw", "arw", "srf", "sr2", "raf", "orf", "rw2", "rwl", "pef", "ptx",
       "dng", "3fr", "fff", "iiq", "x3f", "srw", "erf", "mrw", "kdc", "dcr", "mos", "mef", "gpr"}
JPEG = {"jpg", "jpeg", "hif", "heif", "heic"}
VIDEO = {"mov", "mp4", "m4v", "avi", "mts", "m2ts", "mxf", "insv", "lrv", "360", "braw", "r3d", "crm"}
# The camera's own bookkeeping in its DCIM folder, never a photo: the Insta360 index
# (fileinfo_list.list), Canon's catalogues (.CTG), and the hidden files a computer leaves
# behind (.DS_Store, ._name). Never copied, and never counted as left on the camera, so they
# do not keep the cards from being safe to format. (A video's .THM thumbnail is not one of
# these: it goes with its video.)
HOUSEKEEPING = {"list", "ctg"}
COPY_CHOICES = ("raw", "raw_jpeg", "all")  # RAW only, RAW + JPEG, or everything (videos too)


def copy_choice(cfg: dict) -> str:
    choice = cfg.get("copy_what", "raw_jpeg")
    return choice if choice in COPY_CHOICES else "raw_jpeg"


def is_housekeeping(name: str) -> bool:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return name.startswith(".") or ext in HOUSEKEEPING


def kind_of(name: str) -> str:
    """'raw', 'jpeg', 'video' or 'other' (a sidecar, a voice memo)."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in RAW:
        return "raw"
    if ext in JPEG:
        return "jpeg"
    if ext in VIDEO:
        return "video"
    return "other"


def _stem(f) -> tuple:
    return (f.folder, f.name.rsplit(".", 1)[0].upper())


def choose(files: list, choice: str) -> tuple:
    """Split the files into those to copy and those left on the camera, by the setting.

    'raw' copies RAW files, 'raw_jpeg' RAW and JPEG files, and 'all' everything. Other
    files (an .XMP or a voice memo) go with the photo they belong to.
    """
    files = [f for f in files if not is_housekeeping(f.name)]
    if choice not in COPY_CHOICES or choice == "all":
        return list(files), []
    wanted = {"raw"} if choice == "raw" else {"raw", "jpeg"}
    kept_stems = {_stem(f) for f in files if kind_of(f.name) in wanted}
    keep, skip = [], []
    for f in files:
        kind = kind_of(f.name)
        if kind in wanted or (kind == "other" and _stem(f) in kept_stems):
            keep.append(f)
        else:
            skip.append(f)
    return keep, skip


def jpeg_twins(keep: list, skip: list, choice: str) -> dict:
    """RAW only: the JPEGs the camera made beside a RAW file that is copied, by (folder, stem).

    Some cameras (the Insta360 Ace Pro 2) cannot shoot RAW alone, only RAW + JPEG. With RAW
    only chosen, such a JPEG is not wanted: it never keeps the cards from being safe to
    format while its RAW is safe, and it is read once for the RAW's picture, never kept."""
    if choice != "raw":
        return {}
    raws = {_stem(f) for f in keep if kind_of(f.name) == "raw"}
    return {_stem(f): f for f in skip if kind_of(f.name) == "jpeg" and _stem(f) in raws}


def skipped_summary(skip: list) -> dict:
    out = {"jpeg": 0, "video": 0, "other": 0, "raw": 0, "bytes": 0}
    seen = set()
    for f in skip:
        if (f.name, f.size) in seen:
            continue  # the same file on both of the camera's cards
        seen.add((f.name, f.size))
        out[kind_of(f.name)] += 1
        out["bytes"] += f.size
    out["count"] = len(seen)
    return out


@dataclass
class Plan:
    info: SourceInfo
    found: int  # files on the source
    already: int  # skipped because they were backed up before
    duplicates: int  # skipped because the same photo is on the source twice
    todo: list[SourceFile]
    total_bytes: int
    # One entry per photo on the source, whether or not it is being copied this time.
    photos: list = field(default_factory=list)
    # Files the What to copy setting leaves on the camera, counted by kind.
    skipped: dict = field(default_factory=dict)
    # RAW only: the camera's JPEG beside each RAW, by (folder, stem). Not counted as skipped.
    twins: dict = field(default_factory=dict)


@dataclass
class Result:
    plan: Plan
    shoot_folder: str = ""  # absolute path
    method: str = ""
    verified: int = 0
    failed: int = 0
    bytes_done: int = 0
    seconds: float = 0.0
    interrupted: bool = False
    reason: str = ""
    failures: list = field(default_factory=list)  # (name, note)
    previews_ok: int = 0
    previews_bad: int = 0
    manifest: str = ""
    name: str = ""  # the shoot name used in the folder: what was typed, or 'import'
    relative: str = ""  # the shoot's folder in the library


class Reporter:
    """Receives progress. The command line prints it; the screens will draw it."""

    def plan(self, plan: Plan, shoot_folder: str, method: str) -> None: ...
    def file_start(self, index: int, f: SourceFile) -> None: ...
    def bytes(self, n: int) -> None: ...
    def file_done(self, f: SourceFile, status: str, note: str) -> None: ...
    def mirror_start(self, count: int, nbytes: int, card) -> None: ...


def make_plan(source: Source, ledger, take_all: bool = False, limit: int | None = None,
              on_listing=None, copy: str = "all") -> Plan:
    info = source.info()
    listed = source.list_files(progress=on_listing)
    files, left = choose(listed, copy)
    files = sorted(files, key=lambda f: (f.mtime, f.name, f.folder))
    todo: list[SourceFile] = []
    photos: list[SourceFile] = []
    already = duplicates = 0
    # The camera can write each shot to both card slots. The two copies have the
    # same name and size, but their times can differ by a second or two, because
    # the cards are written one after the other.
    met: dict[tuple, list[int]] = {}
    for f in files:
        times = met.setdefault((f.name, f.size), [])
        if any(abs(f.mtime - t) <= SAME_SHOT_SECONDS for t in times):
            duplicates += 1
            continue
        times.append(f.mtime)
        photos.append(f)
        if not take_all and ledger.seen(info.serial, f.name, f.size, f.mtime, SAME_SHOT_SECONDS):
            already += 1
            continue
        todo.append(f)
    if limit is not None:
        todo = todo[: max(0, limit)]
    twins = jpeg_twins(files, left, copy)
    twin_ids = {id(f) for f in twins.values()}
    summary = skipped_summary([f for f in left if id(f) not in twin_ids and _stem(f) not in twins])
    summary["twins"] = len({(f.name, f.size) for f in left if kind_of(f.name) == "jpeg" and _stem(f) in twins})
    return Plan(info, len(listed), already, duplicates, todo, sum(f.size for f in todo), photos,
                summary, twins)


@dataclass
class Coverage:
    """How well the photos on a camera's card are protected elsewhere."""

    photos: int = 0
    on_tablet: int = 0
    on_second: int = 0  # on the tablet and on Copy 2 as well
    skipped: int = 0  # files the What to copy setting leaves on the camera
    cleared: int = 0  # backed up once, then cleared from the tablet: on neither copy now

    @property
    def copied_safe(self) -> bool:
        """Every file that is copied is in two places."""
        return self.photos > 0 and self.on_tablet == self.photos and self.on_second == self.photos

    @property
    def safe_to_format(self) -> bool:
        """Every file on the cards is in two places: nothing at all would be lost."""
        return self.copied_safe and self.skipped == 0


def coverage(plan: Plan, ledger) -> Coverage:
    """Check every photo on the source against the record.

    The card is safe to format only when each photo is verified on Copy 1 (the tablet)
    and on Copy 2. Nothing else counts.
    """
    cov = Coverage(photos=len(plan.photos), skipped=int((plan.skipped or {}).get("count", 0)))
    for f in plan.photos:
        on_tablet, on_second, cleared = ledger.copy_state(plan.info.serial, f.name, f.size, f.mtime, SAME_SHOT_SECONDS)
        cov.on_tablet += on_tablet
        cov.on_second += on_tablet and on_second
        cov.cleared += cleared
    return cov


_NOT_IN_NAMES = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


STOPPED_MARK = "stopped"  # left in a shoot's .incoming when its backup was stopped part-way


def shoot_slug(name: str) -> str:
    """A typed shoot name as it goes in the folder: lower case, spaces as _, nothing that
    Windows or macOS cannot hold. Nothing typed: 'import'.  'Ramp  Sunset' -> 'ramp_sunset'"""
    text = _NOT_IN_NAMES.sub("", name or "").strip().lower()
    text = re.sub(r"[\s_]+", "_", text).strip("_.")
    return text[:60].rstrip("_.") or "import"


def shoot_folder_for(cfg: dict, date: str, name: str, number: int = 1) -> str:
    """Folder for a shoot, relative to the library: 2026_1015_import_01.

    date is the day of the import (YYYY-MM-DD); the number counts the imports with this
    name on that day."""
    return f"{date[:4]}_{date[5:7]}{date[8:10]}_{shoot_slug(name)}_{number:02d}"


def next_shoot_folder(library, date: str, name: str, ledger=None) -> str:
    """The next free folder for an import with this name on this day: one past the highest
    number on the tablet or in the records, so a deleted shoot's number is not used again."""
    stem = shoot_folder_for({}, date, name, 1)[:-2]
    pick = re.compile(re.escape(stem) + r"(\d{2,})")
    used = []
    try:
        used += [p.name for p in Path(library).iterdir()]
    except OSError:
        pass
    if ledger is not None:
        used += [row["folder"] for row in ledger.list_shoots()]
    numbers = [int(m.group(1)) for m in map(pick.fullmatch, used) if m]
    last = stem + f"{max(numbers, default=0):02d}"
    if numbers and (Path(library) / last / ".incoming" / STOPPED_MARK).exists():
        return last  # stopped part-way: carry on in the same folder
    return stem + f"{max(numbers, default=0) + 1:02d}"


def run_backup(
    source: Source,
    cfg: dict,
    ledger,
    name: str,
    take_all: bool = False,
    date: str | None = None,
    limit: int | None = None,
    reporter: Reporter | None = None,
    plan: Plan | None = None,
    folder: str | None = None,
) -> Result:
    """Copy new photos from an open source into the library.

    Safe to interrupt and to run again: finished files are skipped, and a
    file that was part-way through continues from where it stopped.
    """
    reporter = reporter or Reporter()
    library = Path(cfg["library"])
    library.mkdir(parents=True, exist_ok=True)

    plan = plan or make_plan(source, ledger, take_all=take_all, limit=limit, copy=copy_choice(cfg))
    result = Result(plan=plan)
    if not plan.todo:
        return result

    free = shutil.disk_usage(library).free
    if plan.total_bytes + SPACE_MARGIN > free:
        from .util import human_bytes

        raise BackupError(
            f"Not enough space on the tablet: this backup needs {human_bytes(plan.total_bytes)} "
            f"and {human_bytes(free)} is free."
        )

    # Named by the day of the import and what was typed (or 'import'), numbered: each
    # import is a folder of its own. A backup stopped part-way is carried on in its folder.
    shoot_date = date or local_date(time.time())
    name = shoot_slug(name)
    result.name = name
    rel = folder or next_shoot_folder(library, shoot_date, name, ledger)
    shoot_dir = library / rel
    incoming = shoot_dir / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    try:
        (incoming / STOPPED_MARK).unlink()
    except OSError:
        pass
    shoot_id = ledger.get_or_create_shoot(rel, name, shoot_date, plan.info.model, plan.info.serial)
    result.relative = rel
    result.shoot_folder = str(shoot_dir)

    # New names, when renaming on import is on. The camera's name stays in the records.
    targets = {}
    pattern = cfg.get("rename_pattern") or naming.DEFAULT_PATTERN
    if cfg.get("rename_on_import") and not naming.check(pattern):
        here = [r for r in ledger.shoot_files(shoot_id) if r["status"] == "verified"]
        # The numbers carry on across the day's imports with this name: _01 has 0001-0040,
        # _02 starts at 0041, so no two photos of the day share a new name.
        stems = set()
        for row in ledger.list_shoots():
            if row["date"] == shoot_date and row["name"] == name:
                stems |= {(row["id"], naming.stem_of(r["src_name"]).lower())
                          for r in ledger.shoot_files(row["id"]) if r["status"] == "verified"}
        start = len(stems) + 1
        taken = {r["rel_name"] or r["src_name"] for r in here}
        try:
            taken |= {p.name for p in shoot_dir.iterdir()}
        except OSError:
            pass
        targets = naming.plan_names([(i, f.name, f.mtime) for i, f in enumerate(plan.todo)], pattern, name,
                                    plan.info.model, start=start, taken_names=taken)

    wanted = cfg.get("download_method", "auto")
    use_chunks = source.supports_chunks and wanted in ("auto", "chunked")
    chunk_size = max(1, int(cfg.get("chunk_mb", 8))) * 1024 * 1024
    result.method = "chunked" if use_chunks else "whole"
    reporter.plan(plan, str(shoot_dir), result.method)

    log = _open_log(ledger)
    _log(log, event="start", shoot=rel, source=plan.info.model, serial=plan.info.serial,
         found=plan.found, new=len(plan.todo), bytes=plan.total_bytes, method=result.method)

    started = time.time()
    try:
        for index, f in enumerate(plan.todo, start=1):
            target = targets.get(index - 1)
            reporter.file_start(index, f)
            part = incoming / f"{f.name}.{f.size}.{f.mtime}.part"
            t0 = time.time()
            sha, used_chunks = _fetch(source, f, part, use_chunks, chunk_size, reporter.bytes)
            if use_chunks and not used_chunks:
                # The camera turned out not to support reading in pieces.
                use_chunks = False
                result.method = "whole"
            status, note, rel_name, preview = _finalize(f, part, shoot_dir, sha, target)
            ledger.add_file(shoot_id, plan.info.serial, f, sha, rel_name, status, note, preview)
            twin = plan.twins.get(_stem(f)) if status == "verified" else None
            if twin is not None:
                _pictures_from_twin(source, ledger, shoot_id, plan.info.serial, f, twin, incoming,
                                    use_chunks, chunk_size)
            if status == "verified":
                result.verified += 1
                result.bytes_done += f.size
                if preview is True:
                    result.previews_ok += 1
                elif preview is False:
                    result.previews_bad += 1
            else:
                result.failed += 1
                result.failures.append((f.name, note))
            _log(log, event="file", name=f.name, size=f.size, status=status, note=note,
                 seconds=round(time.time() - t0, 3))
            reporter.file_done(f, status, note)
    except (KeyboardInterrupt, StopRequested):
        result.interrupted, result.reason = True, STOPPED
    except SourceError as exc:
        result.interrupted, result.reason = True, str(exc)
    except OSError as exc:
        result.interrupted, result.reason = True, f"Could not write to the tablet: {exc}"

    finished = time.time()
    result.seconds = finished - started
    ledger.add_run(shoot_id, started, finished, plan.info.model, plan.info.serial, result.method,
                   plan.found, len(plan.todo), result.verified, result.failed, result.bytes_done,
                   result.seconds, result.interrupted, result.reason)
    result.manifest = str(write_manifest(ledger, shoot_id))
    _log(log, event="end", verified=result.verified, failed=result.failed, bytes=result.bytes_done,
         seconds=round(result.seconds, 1), interrupted=result.interrupted, reason=result.reason)
    if log:
        log.close()
    try:
        if result.interrupted:
            (incoming / STOPPED_MARK).touch()  # the next backup with this name carries on here
        else:
            incoming.rmdir()  # only succeeds when nothing is left part-way
    except OSError:
        pass
    return result


# ----- one file ---------------------------------------------------------------


def _pictures_from_twin(source, ledger, shoot_id, serial, f, twin, incoming, use_chunks, chunk_size) -> bool:
    """Read the camera's JPEG beside a RAW just copied and make the RAW's thumbnail and
    screen picture from it. The JPEG is not kept: it is read into the shoot's .incoming
    folder, made into the two pictures, and removed. Best effort: without it the RAW's
    picture is made from the RAW itself when it is first looked at."""
    from . import photos

    part = incoming / f".twin-{twin.name}.jpg"
    try:
        row = ledger.db.execute(
            "SELECT id FROM files WHERE shoot_id=? AND src_serial=? AND src_name=? AND size=? AND mtime=? "
            "AND status='verified' ORDER BY id DESC LIMIT 1", (shoot_id, serial, f.name, f.size, f.mtime)).fetchone()
        if row is None or not photos.have_pillow():
            return False
        if part.exists():
            part.unlink()
        _fetch(source, twin, part, use_chunks, chunk_size, lambda n: None)
        cache = Path(ledger.dir) / "previews" / str(row["id"])
        made = [photos.picture(part, size, cache) for size in ("thumb", "screen")]
        return all(made)
    except (StopRequested, KeyboardInterrupt):
        raise
    except Exception:
        return False
    finally:
        try:
            part.unlink()
        except OSError:
            pass


def _fetch(source: Source, f: SourceFile, part: Path, use_chunks: bool, chunk_size: int, on_bytes):
    """Download one file to `part`. Returns (sha256 of the bytes received, used_chunks)."""
    if use_chunks:
        h = hashlib.sha256()
        offset = 0
        if part.exists():
            # Continue a file that was part-way through. Every piece is flushed to
            # disk as it is written, so everything except the last two pieces is
            # known to be good. Drop those and carry on from there.
            have = part.stat().st_size
            keep = max(0, (have // chunk_size - 1) * chunk_size)
            if keep and keep < f.size:
                with open(part, "r+b") as fh:
                    fh.truncate(keep)
                with open(part, "rb") as fh:
                    while True:
                        block = fh.read(4 * 1024 * 1024)
                        if not block:
                            break
                        h.update(block)
                offset = keep
                on_bytes(keep)
            else:
                part.unlink()
        try:
            with open(part, "ab") as out:
                while offset < f.size:
                    data = source.read_chunk(f, offset, min(chunk_size, f.size - offset))
                    if len(data) == 0:
                        break
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                    h.update(data)
                    offset += len(data)
                    on_bytes(len(data))
            return h.hexdigest(), True
        except NotSupported:
            on_bytes(-offset)
            # fall through to a whole-file download
    data = source.read_whole(f)
    with open(part, "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    on_bytes(len(data))
    return hashlib.sha256(data).hexdigest(), False


def _finalize(f: SourceFile, part: Path, shoot_dir: Path, sha: str, target: str | None = None):
    """Check a downloaded file and move it into place, under 'target' when renaming.

    Returns (status, note, file name in the shoot folder, preview readable?).
    """
    got = part.stat().st_size
    if got != f.size:
        part.unlink()
        return "failed", f"size mismatch: received {got} bytes, the camera reported {f.size}", None, None

    ok, why = checks.check_container(part, f.name)
    if ok is False:
        part.unlink()
        return "failed", why, None, None

    note = ""
    wanted = target or f.name
    final = shoot_dir / wanted
    if final.exists():
        if final.stat().st_size == f.size and checks.sha256_file(final) == sha:
            # Exactly this photo is already here. Nothing to add.
            part.unlink()
            return "verified", "already present", wanted, _preview_ok(final, f.name)
        # Same name, different photo (the camera's numbering wrapped). Keep both.
        stem, ext = os.path.splitext(wanted)
        n = 2
        while (shoot_dir / f"{stem}_{n}{ext}").exists():
            n += 1
        final = shoot_dir / f"{stem}_{n}{ext}"
        note = f"kept as {final.name}: another photo is already named {wanted}"

    os.replace(part, final)
    _fsync_dir(shoot_dir)
    # Keep the time the photo was taken as the file's time.
    try:
        os.utime(final, (f.mtime, f.mtime))
    except OSError:
        pass
    return "verified", note, final.name, _preview_ok(final, f.name)


def _preview_ok(path: Path, name: str):
    if Path(name).suffix.lower() not in (".nef", ".nrw", ".dng"):
        return None
    return checks.find_embedded_jpeg(path) is not None


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


# ----- run log ----------------------------------------------------------------


def _open_log(ledger):
    try:
        logs = ledger.dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        old = sorted(logs.glob("backup-*.log"))
        for stale in old[:-19]:
            stale.unlink()
        return open(logs / time.strftime("backup-%Y%m%d-%H%M%S.log"), "a", encoding="utf-8")
    except OSError:
        return None


def _log(log, **fields) -> None:
    if not log:
        return
    fields["t"] = round(time.time(), 3)
    try:
        log.write(json.dumps(fields) + "\n")
        log.flush()
    except OSError:
        pass
