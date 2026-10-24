"""A picture made from the RAW data itself, for DNG files that keep no preview inside.

Most cameras store a JPEG preview in each RAW file, and the viewer uses that. A few (the
Insta360 Ace Pro 2 among them) store only the sensor data: an uncompressed 16-bit Bayer
mosaic. For those, this module makes a quick picture at half the sensor's size or less:
each 2x2 block of the mosaic becomes one pixel, with the camera's own black level, white
level and white balance, and a standard display curve.

It is a preview for choosing and checking photos, not a RAW conversion: the colours come
from the camera's white balance without its colour matrix, and nothing is sharpened. The
file itself is only read, never changed.
"""

from __future__ import annotations

import math
import os
import struct

from .checks import _ifds, _longs, _one, _open_tiff

CFA_PHOTOMETRIC = 32803
MAX_PIXELS = 120_000_000


def _rationals(fh, bo, entry, fsize) -> list:
    typ, n, value = entry
    if typ not in (5, 10) or n == 0 or n > 64:
        return []
    where = struct.unpack(bo + "I", value)[0]
    if where + 8 * n > fsize:
        return []
    fh.seek(where)
    nums = struct.unpack(bo + ("I" if typ == 5 else "i") * (2 * n), fh.read(8 * n))
    return [a / b if b else 0.0 for a, b in zip(nums[::2], nums[1::2])]


def _bytes(fh, bo, entry, fsize) -> bytes:
    typ, n, value = entry
    if n <= 4:
        return value[:n]
    where = struct.unpack(bo + "I", value)[0]
    if where + n > fsize:
        return b""
    fh.seek(where)
    return fh.read(n)


def find_mosaic(path):
    """The sensor data of a DNG that this module can draw, or None.

    Returns a dict: width, height, offset, byteorder, cfa (four colours for the 2x2
    block, 0 red, 1 green, 2 blue), black, white, gains (red, green, blue), exposure.
    """
    try:
        fsize = os.path.getsize(path)
        with open(path, "rb") as fh:
            bo, first = _open_tiff(fh)
            if bo is None:
                return None
            ifds = list(_ifds(fh, bo, first, fsize))
            neutral = []
            exposure = 0.0
            for tags in ifds:  # DNG-wide tags live in IFD 0
                if 0xC628 in tags:
                    neutral = _rationals(fh, bo, tags[0xC628], fsize)
                if 0xC62A in tags:
                    values = _rationals(fh, bo, tags[0xC62A], fsize)
                    exposure = values[0] if values else 0.0
            for tags in ifds:
                if (_one(fh, bo, tags, 0x0106, fsize) != CFA_PHOTOMETRIC or _one(fh, bo, tags, 0x0103, fsize, 1) != 1
                        or _one(fh, bo, tags, 0x0102, fsize) != 16 or _one(fh, bo, tags, 0x0115, fsize, 1) != 1):
                    continue
                width, height = _one(fh, bo, tags, 0x0100, fsize, 0), _one(fh, bo, tags, 0x0101, fsize, 0)
                if not width or not height or width % 2 or height % 2 or width * height > MAX_PIXELS:
                    continue
                if _longs(fh, bo, tags.get(0x828D, (3, 0, b"")), fsize) not in ([2, 2], []):
                    continue
                cfa = list(_bytes(fh, bo, tags[0x828E], fsize)) if 0x828E in tags else []
                if len(cfa) != 4 or 0 not in cfa or 1 not in cfa or 2 not in cfa:
                    continue
                starts = _longs(fh, bo, tags[0x0111], fsize) if 0x0111 in tags else []
                lengths = _longs(fh, bo, tags[0x0117], fsize) if 0x0117 in tags else []
                if not starts or len(starts) != len(lengths):
                    continue
                # One run of data, row after row: several strips are fine when they follow on.
                if any(starts[i] + lengths[i] != starts[i + 1] for i in range(len(starts) - 1)):
                    continue
                if sum(lengths) < width * height * 2 or starts[0] + width * height * 2 > fsize:
                    continue
                black = 0.0
                if 0xC61A in tags:
                    levels = _rationals(fh, bo, tags[0xC61A], fsize) or _longs(fh, bo, tags[0xC61A], fsize)
                    black = sum(levels) / len(levels) if levels else 0.0
                white = float(_one(fh, bo, tags, 0xC61D, fsize, 65535))
                if len(neutral) == 3 and all(v > 0 for v in neutral):
                    gains = [neutral[1] / neutral[0], 1.0, neutral[1] / neutral[2]]
                else:
                    gains = [1.0, 1.0, 1.0]
                return {"width": width, "height": height, "offset": starts[0], "byteorder": bo, "cfa": cfa,
                        "black": black, "white": max(white, black + 1), "gains": gains, "exposure": exposure}
    except (OSError, struct.error, KeyError):
        return None
    return None


def _curve(black: float, white: float, gain: float) -> list:
    """16-bit sensor value -> 8-bit display value: black removed, white balance and
    exposure applied, then the sRGB curve."""
    span = white - black
    table = []
    for v in range(65536):
        x = (v - black) / span * gain
        if x <= 0.0:
            table.append(0)
        elif x >= 1.0:
            table.append(255)
        else:
            y = 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055
            table.append(int(y * 255 + 0.5))
    return table


_curves: dict = {}

MID_GREY = 0.13  # where the middle of a photo's brightness is put, before the display curve


def _auto_exposure(green, m, boost: float) -> float:
    """How much brighter to make the picture, from its middle brightness: a camera's own
    preview is exposed for the screen, and the bare sensor data is darker than that."""

    small = green.reduce(max(1, max(green.size) // 256)) if max(green.size) > 256 else green
    scale = 255.0 / (m["white"] - m["black"]) * boost
    try:
        linear = small.point(lambda v: v * scale - m["black"] * scale).convert("L")
    except Exception:
        return 1.0
    counts = linear.histogram()
    total, seen, median = sum(counts), 0, 0
    for level, n in enumerate(counts):
        seen += n
        if seen >= total / 2:
            median = level
            break
    if median <= 0:
        return 4.0
    return min(6.0, max(0.7, MID_GREY * 255 / median))


def render(path, long_side: int | None = None):
    """A Pillow RGB picture of the sensor data, at most long_side pixels on its longer side
    (at most half the sensor's width), or None when the file is not one this can draw."""
    m = find_mosaic(path)
    if m is None:
        return None
    from PIL import Image, ImageMath

    w, h = m["width"], m["height"]
    half = (w // 2, h // 2)
    with open(path, "rb") as fh:
        fh.seek(m["offset"])
        raw = bytearray(fh.read(w * h * 2))
    row = w * 2
    raw += bytes(row * 2)  # room for the last row pair's stride
    data = memoryview(raw)
    # Each 2x2 block as one pixel: read the mosaic's even and odd rows as 4-byte groups,
    # so the bands are (low, high) bytes of the left pixel and of the right one.
    rows = [Image.frombuffer("RGBA", half, data[start:], "raw", "RGBA", row * 2, 1) for start in (0, row)]
    lo_hi = (0, 1) if m["byteorder"] == "<" else (1, 0)

    def plane(position):
        rows_img = rows[position // 2]
        bands = rows_img.split()
        first = 2 * (position % 2)
        lo, hi = bands[first + lo_hi[0]], bands[first + lo_hi[1]]
        img = ImageMath.lambda_eval(lambda a: a["hi"] * 256 + a["lo"], hi=hi, lo=lo) \
            if hasattr(ImageMath, "lambda_eval") else ImageMath.eval("hi * 256 + lo", hi=hi, lo=lo)
        img = img.convert("I")
        if long_side and max(half) > long_side:
            factor = max(1, math.ceil(max(half) / long_side))
            img = img.reduce(factor)
        return img

    boost = 2.0 ** m["exposure"] * _auto_exposure(plane(m["cfa"].index(1)), m, 2.0 ** m["exposure"])
    out = []
    for colour in (0, 1, 2):
        position = m["cfa"].index(colour)
        gain = m["gains"][colour] * boost
        key = (round(m["black"]), round(m["white"]), round(gain, 4))
        if key not in _curves:
            _curves.clear() if len(_curves) > 6 else None
            _curves[key] = _curve(m["black"], m["white"], gain)
        img = plane(position)
        try:
            out.append(img.point(_curves[key], "L"))
        except (ValueError, TypeError):
            # An older Pillow without 16-bit look-up tables: scale first, then the curve.
            scale = 255.0 / (m["white"] - m["black"]) * gain
            linear = img.point(lambda v: v * scale - m["black"] * scale).convert("L")
            out.append(linear.point([int(255 * (i / 255) ** (1 / 2.2) + 0.5) for i in range(256)]))
    from PIL import ImageEnhance

    # Without the camera's colour matrix the colours come out muted: lift them a little.
    return ImageEnhance.Color(Image.merge("RGB", out)).enhance(1.35)
