"""Checks on a file once it has landed on the tablet."""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

_BUF = 4 * 1024 * 1024

_TIFF_LIKE = {".nef", ".nrw", ".tif", ".tiff", ".dng"}
_JPEG = {".jpg", ".jpeg"}
_QUICKTIME = {".mov", ".mp4"}


def sha256_file(path: os.PathLike, limit: int | None = None) -> str:
    """SHA-256 of a file, or of its first `limit` bytes."""
    h = hashlib.sha256()
    remaining = limit
    with open(path, "rb") as fh:
        while True:
            want = _BUF if remaining is None else min(_BUF, remaining)
            if want == 0:
                break
            block = fh.read(want)
            if not block:
                break
            h.update(block)
            if remaining is not None:
                remaining -= len(block)
    return h.hexdigest()


def check_container(path: os.PathLike, name: str) -> tuple[bool | None, str]:
    """Does the file start the way its type should?

    Returns (True/False, reason), or (None, "") for types we have no check for.
    """
    ext = Path(name).suffix.lower()
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError as exc:
        return False, f"could not reopen the file: {exc}"
    if ext in _TIFF_LIKE:
        ok = head[:4] in (b"II*\x00", b"MM\x00*")
        return ok, "" if ok else "does not start like a RAW file"
    if ext in _JPEG:
        ok = head[:2] == b"\xff\xd8"
        return ok, "" if ok else "does not start like a JPEG"
    if ext in _QUICKTIME:
        ok = head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip")
        return ok, "" if ok else "does not start like a video file"
    return None, ""


def _ifds(fh, bo: str, first: int, fsize: int):
    """Every IFD in the file, the sub-IFDs included, at most 32."""
    queue, seen = [first], set()
    while queue and len(seen) < 32:
        offset = queue.pop(0)
        if offset in seen or offset <= 0 or offset + 2 > fsize:
            continue
        seen.add(offset)
        tags, next_ifd = _read_ifd(fh, bo, offset, fsize)
        if next_ifd:
            queue.append(next_ifd)
        if 0x014A in tags:  # SubIFDs
            queue.extend(_longs(fh, bo, tags[0x014A], fsize))
        yield tags


def _one(fh, bo, tags, tag, fsize, default=None):
    if tag not in tags:
        return default
    values = _longs(fh, bo, tags[tag], fsize)
    return values[0] if values else default


def _open_tiff(fh):
    head = fh.read(8)
    if head[:4] == b"II*\x00":
        bo = "<"
    elif head[:4] == b"MM\x00*":
        bo = ">"
    else:
        return None, 0
    return bo, struct.unpack(bo + "I", head[4:8])[0]


def find_embedded_jpeg(path: os.PathLike) -> tuple[int, int] | None:
    """Locate the largest JPEG preview inside a TIFF-based RAW file (NEF, DNG).

    Nikon keeps it behind JPEGInterchangeFormat; many DNGs (Insta360, phones, Adobe's
    converter) keep it as a one-strip JPEG image of its own. Returns (offset, length), or
    None if no readable preview was found. The viewer uses it to show photos without
    decoding RAW data.
    """
    try:
        fsize = os.path.getsize(path)
        with open(path, "rb") as fh:
            bo, first = _open_tiff(fh)
            if bo is None:
                return None
            best: tuple[int, int] | None = None
            for tags in list(_ifds(fh, bo, first, fsize)):
                found = []
                if 0x0201 in tags and 0x0202 in tags:  # JPEG offset and length
                    start = _longs(fh, bo, tags[0x0201], fsize)
                    length = _longs(fh, bo, tags[0x0202], fsize)
                    if start and length:
                        found.append((start[0], length[0]))
                # A preview stored as a JPEG image: baseline JPEG (6 or 7), RGB or YCbCr.
                # Not the RAW data itself, which is lossless JPEG with a CFA or linear layout.
                if (_one(fh, bo, tags, 0x0103, fsize) in (6, 7)
                        and _one(fh, bo, tags, 0x0106, fsize, 6) in (2, 6)
                        and 0x0111 in tags and 0x0117 in tags):
                    starts = _longs(fh, bo, tags[0x0111], fsize)
                    lengths = _longs(fh, bo, tags[0x0117], fsize)
                    if len(starts) == 1 and len(lengths) == 1:
                        found.append((starts[0], lengths[0]))
                for start, length in found:
                    if start + length <= fsize and length > 2:
                        fh.seek(start)
                        if fh.read(2) == b"\xff\xd8" and (best is None or length > best[1]):
                            best = (start, length)
            return best
    except (OSError, struct.error):
        return None


def find_rgb_preview(path: os.PathLike):
    """An uncompressed 8-bit RGB preview inside a DNG, as (width, height, [(offset, length)]),
    or None. Some cameras store only this kind, with no JPEG at all. The largest is chosen."""
    try:
        fsize = os.path.getsize(path)
        with open(path, "rb") as fh:
            bo, first = _open_tiff(fh)
            if bo is None:
                return None
            best = None
            for tags in list(_ifds(fh, bo, first, fsize)):
                if (_one(fh, bo, tags, 0x0103, fsize, 1) != 1 or _one(fh, bo, tags, 0x0106, fsize) != 2
                        or _one(fh, bo, tags, 0x0115, fsize, 1) != 3 or _one(fh, bo, tags, 0x011C, fsize, 1) != 1):
                    continue
                bits = _longs(fh, bo, tags[0x0102], fsize) if 0x0102 in tags else [8]
                if any(b != 8 for b in bits):
                    continue
                width, height = _one(fh, bo, tags, 0x0100, fsize, 0), _one(fh, bo, tags, 0x0101, fsize, 0)
                starts = _longs(fh, bo, tags[0x0111], fsize) if 0x0111 in tags else []
                lengths = _longs(fh, bo, tags[0x0117], fsize) if 0x0117 in tags else []
                if not (width and height and starts and len(starts) == len(lengths)):
                    continue
                if sum(lengths) < width * height * 3 or any(a + b > fsize for a, b in zip(starts, lengths)):
                    continue
                if width * height > 40_000_000:
                    continue
                if best is None or width * height > best[0] * best[1]:
                    best = (width, height, list(zip(starts, lengths)))
            return best
    except (OSError, struct.error):
        return None


def _read_ifd(fh, bo: str, offset: int, fsize: int):
    fh.seek(offset)
    raw = fh.read(2)
    if len(raw) < 2:
        return {}, 0
    count = struct.unpack(bo + "H", raw)[0]
    if count == 0 or count > 2000 or offset + 2 + count * 12 + 4 > fsize + 4:
        return {}, 0
    data = fh.read(count * 12 + 4)
    tags = {}
    for i in range(count):
        entry = data[i * 12 : (i + 1) * 12]
        if len(entry) < 12:
            break
        tag, typ, n = struct.unpack(bo + "HHI", entry[:8])
        tags[tag] = (typ, n, entry[8:12])
    tail = data[count * 12 : count * 12 + 4]
    next_ifd = struct.unpack(bo + "I", tail)[0] if len(tail) == 4 else 0
    return tags, next_ifd


def _longs(fh, bo: str, entry, fsize: int) -> list[int]:
    typ, n, value = entry
    unit = {3: 2, 4: 4, 13: 4}.get(typ)
    if unit is None or n == 0 or n > 64:
        return []
    fmt = bo + ("H" if unit == 2 else "I") * n
    if unit * n <= 4:
        return list(struct.unpack(bo + ("H" if unit == 2 else "I") * n, value[: unit * n]))
    where = struct.unpack(bo + "I", value)[0]
    if where + unit * n > fsize:
        return []
    fh.seek(where)
    return list(struct.unpack(fmt, fh.read(unit * n)))
