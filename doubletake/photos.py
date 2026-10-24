"""Pictures for the viewer, taken from the JPEG preview stored inside each RAW file.

A DNG with no preview inside (the Insta360 Ace Pro 2's) is drawn from its sensor data by
rawview instead. Nothing here changes a photo.
Thumbnails and screen-size pictures are made once and kept in the library's
own folder, so browsing a shoot again is quick.
"""

from __future__ import annotations

import io
import os
import struct
import threading
from pathlib import Path

from .checks import _longs, _read_ifd, find_embedded_jpeg, find_rgb_preview

SIZES = {"thumb": 360, "screen": 2048}
_ROTATE = {3: 180, 6: 270, 8: 90}  # EXIF orientation -> degrees to turn, counter-clockwise
_RAW = {".nef", ".nrw", ".dng", ".tif", ".tiff"}
_JPEG = {".jpg", ".jpeg"}

# The m5 has two cores. Two pictures at a time keeps the screens responsive.
_working = threading.BoundedSemaphore(2)
_full_lock = threading.Lock()
_full_cache: dict = {}  # the last full-size picture, for panning around at 100%


def have_pillow() -> bool:
    import importlib.util

    return importlib.util.find_spec("PIL") is not None


# ----- reading what the camera stored -------------------------------------------


def _ascii(fh, bo, entry, fsize) -> str:
    typ, n, value = entry
    if typ != 2 or n == 0 or n > 256:
        return ""
    if n <= 4:
        raw = value[:n]
    else:
        where = struct.unpack(bo + "I", value)[0]
        if where + n > fsize:
            return ""
        fh.seek(where)
        raw = fh.read(n)
    return raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()


def _rational(fh, bo, entry, fsize):
    typ, n, value = entry
    if typ not in (5, 10) or n < 1:
        return None
    where = struct.unpack(bo + "I", value)[0]
    if where + 8 > fsize:
        return None
    fh.seek(where)
    num, den = struct.unpack(bo + ("ii" if typ == 10 else "II"), fh.read(8))
    return (num, den) if den else None


def _short(entry, bo):
    typ, n, value = entry
    if typ == 3:
        return struct.unpack(bo + "H", value[:2])[0]
    if typ == 4:
        return struct.unpack(bo + "I", value)[0]
    return None


def read_details(path) -> dict:
    """Exposure details and orientation from a RAW file's own records."""
    out: dict = {}
    try:
        fsize = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(8)
            bo = "<" if head[:4] == b"II*\x00" else ">" if head[:4] == b"MM\x00*" else None
            if bo is None:
                return out
            tags, _ = _read_ifd(fh, bo, struct.unpack(bo + "I", head[4:8])[0], fsize)
            if 0x0112 in tags:
                out["orientation"] = _short(tags[0x0112], bo) or 1
            if 0x0110 in tags:
                out["model"] = _ascii(fh, bo, tags[0x0110], fsize)
            if 0x8769 not in tags:
                return out
            exif_at = _longs(fh, bo, tags[0x8769], fsize)
            if not exif_at:
                return out
            exif, _ = _read_ifd(fh, bo, exif_at[0], fsize)
            shutter = _rational(fh, bo, exif[0x829A], fsize) if 0x829A in exif else None
            if shutter:
                num, den = shutter
                seconds = num / den
                out["shutter"] = f"1/{round(den / num)} s" if 0 < seconds < 0.5 else f"{seconds:g} s"
            aperture = _rational(fh, bo, exif[0x829D], fsize) if 0x829D in exif else None
            if aperture:
                out["aperture"] = f"f/{aperture[0] / aperture[1]:.1f}".replace(".0", "")
            if 0x8827 in exif:
                iso = _short(exif[0x8827], bo)
                if iso:
                    out["iso"] = f"ISO {iso}"
            focal = _rational(fh, bo, exif[0x920A], fsize) if 0x920A in exif else None
            if focal:
                out["focal"] = f"{round(focal[0] / focal[1])} mm"
            if 0x9003 in exif:
                out["taken"] = _ascii(fh, bo, exif[0x9003], fsize)
            if 0xA434 in exif:
                out["lens"] = _ascii(fh, bo, exif[0xA434], fsize)
    except (OSError, struct.error, KeyError, ZeroDivisionError):
        pass
    return out


def preview_jpeg(path) -> bytes | None:
    """The largest JPEG stored inside the file, or the file itself when it is a JPEG."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext in _JPEG:
            return path.read_bytes()
        if ext in _RAW:
            where = find_embedded_jpeg(path)
            if where:
                with open(path, "rb") as fh:
                    fh.seek(where[0])
                    return fh.read(where[1])
            return _rgb_preview_as_jpeg(path)
    except OSError:
        pass
    return None


def _rgb_preview_as_jpeg(path) -> bytes | None:
    """A DNG whose only preview is uncompressed RGB: that preview, made into a JPEG."""
    found = find_rgb_preview(path)
    if not found or not have_pillow():
        return None
    width, height, strips = found
    from PIL import Image

    data = bytearray()
    with open(path, "rb") as fh:
        for start, length in strips:
            fh.seek(start)
            data += fh.read(length)
    try:
        img = Image.frombytes("RGB", (width, height), bytes(data[: width * height * 3]))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=92)
        return out.getvalue()
    except Exception:
        return None


# ----- making pictures ---------------------------------------------------------------


def _open(path, long_side: int | None = None):
    from PIL import Image

    data = preview_jpeg(path)
    if data is None:
        if Path(path).suffix.lower() == ".dng":
            from . import rawview

            img = rawview.render(path, long_side)
            if img is not None:
                return img, read_details(path).get("orientation", 1)
        return None, 1
    img = Image.open(io.BytesIO(data))
    orientation = 1
    if Path(path).suffix.lower() in _RAW:
        orientation = read_details(path).get("orientation", 1)
    else:
        try:
            orientation = img.getexif().get(0x0112, 1)
        except Exception:
            orientation = 1
    return img, orientation


def _turn(img, orientation):
    degrees = _ROTATE.get(orientation)
    return img.rotate(degrees, expand=True) if degrees else img


def picture(path, size: str, cache_dir) -> Path | None:
    """A thumbnail or screen-size picture, made once and then kept. None when there is no preview."""
    long_side = SIZES[size]
    cache = Path(cache_dir)
    target = cache / f"{size}.jpg"
    if target.is_file():
        return target
    if not have_pillow():
        return None
    with _working:
        if target.is_file():
            return target
        try:
            img, orientation = _open(path, long_side)
            if img is None:
                return None
            img.draft("RGB", (long_side, long_side))  # decode at a fraction of full size: much faster
            img = _turn(img.convert("RGB"), orientation)
            img.thumbnail((long_side, long_side))
            cache.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.{threading.get_ident()}.tmp")
            img.save(tmp, "JPEG", quality=82 if size == "thumb" else 88)
            os.replace(tmp, target)
            return target
        except Exception:
            return None


def full_size(path):
    """The full-size preview as (width, height), or None."""
    if not have_pillow():
        return None
    try:
        img, orientation = _open(path)
        if img is None:
            return None
        w, h = img.size
        return (h, w) if orientation in (6, 8) else (w, h)
    except Exception:
        return None


def crop(path, cx: float, cy: float, width: int, height: int, scale: float = 1.0) -> bytes | None:
    """A piece of the full-size preview, centred on (cx, cy) given as fractions.

    'width' and 'height' are in the preview's own pixels; 'scale' is the zoom level
    the piece is sent at: 1 for 100%, 0.75 or 0.5 for 75% and 50%.
    """
    if not have_pillow():
        return None
    key = str(path)
    with _working:
        with _full_lock:
            img = _full_cache.get(key)
        if img is None:
            try:
                img, orientation = _open(path)
                if img is None:
                    return None
                img = _turn(img.convert("RGB"), orientation)
            except Exception:
                return None
            with _full_lock:
                _full_cache.clear()  # one at a time: a 45 MP picture takes about 140 MB
                _full_cache[key] = img
        w, h = img.size
        width, height = max(64, min(width, w)), max(64, min(height, h))
        left = int(min(max(0, cx * w - width / 2), w - width))
        top = int(min(max(0, cy * h - height / 2), h - height))
        piece = img.crop((left, top, left + width, top + height))
        if scale < 0.999:
            from PIL import Image
            piece = piece.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.LANCZOS)
        out = io.BytesIO()
        piece.save(out, "JPEG", quality=90)
        return out.getvalue()


def small(path, long_side: int) -> bytes | None:
    """A small picture of a JPEG, made on the spot (for exports, which are not in the records)."""
    if not have_pillow():
        return None
    from PIL import Image

    with _working:
        try:
            img = Image.open(path)
            img.draft("RGB", (long_side, long_side))
            img = img.convert("RGB")
            img.thumbnail((long_side, long_side))
            out = io.BytesIO()
            img.save(out, "JPEG", quality=82)
            return out.getvalue()
        except Exception:
            return None


def forget(cache_dir) -> None:
    """Remove the kept pictures for one photo."""
    cache = Path(cache_dir)
    for size in SIZES:
        try:
            (cache / f"{size}.jpg").unlink()
        except OSError:
            pass
    try:
        cache.rmdir()
    except OSError:
        pass
