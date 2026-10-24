"""Builds stand-in camera cards for the tests."""

from __future__ import annotations

import os
import struct
from pathlib import Path


def fake_nef(size: int = 300_000, seed: int = 0) -> bytes:
    """A small file shaped like a NEF: a TIFF with a JPEG preview in a sub-IFD."""
    jpeg = b"\xff\xd8" + bytes((seed + i) % 251 for i in range(2000)) + b"\xff\xd9"
    # Layout: header(8) | IFD0 | SubIFD | jpeg | padding
    ifd0_off = 8
    ifd0_len = 2 + 1 * 12 + 4
    sub_off = ifd0_off + ifd0_len
    sub_len = 2 + 2 * 12 + 4
    jpeg_off = sub_off + sub_len
    out = bytearray(b"II*\x00" + struct.pack("<I", ifd0_off))
    out += struct.pack("<H", 1) + struct.pack("<HHII", 0x014A, 4, 1, sub_off) + struct.pack("<I", 0)
    out += struct.pack("<H", 2)
    out += struct.pack("<HHII", 0x0201, 4, 1, jpeg_off)
    out += struct.pack("<HHII", 0x0202, 4, 1, len(jpeg))
    out += struct.pack("<I", 0)
    out += jpeg
    rest = size - len(out)
    block = bytes((seed * 7 + i) % 256 for i in range(4096))
    out += (block * (rest // 4096 + 1))[:rest]
    return bytes(out)


def make_card(root: Path, count: int = 6, size: int = 300_000, start: int = 4821,
              folder: str = "DCIM/100ND850", base_time: int = 1_791_700_000) -> list[Path]:
    """Write `count` fake NEFs into a card-like folder tree. Returns their paths."""
    target = Path(root) / folder
    target.mkdir(parents=True, exist_ok=True)
    made = []
    for i in range(count):
        path = target / f"DSC_{start + i:04d}.NEF"
        path.write_bytes(fake_nef(size + i * 1000, seed=start + i))
        os.utime(path, (base_time + i * 2, base_time + i * 2))
        made.append(path)
    return made


def jpeg_bytes(width: int = 1200, height: int = 800, seed: int = 0) -> bytes:
    """A real JPEG, with a pattern that has sharp edges to look at at 100%."""
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), (40 + seed % 80, 90, 140))
    draw = ImageDraw.Draw(img)
    for x in range(0, width, 40):
        draw.line([(x, 0), (x, height)], fill=(230, 230, 230), width=2)
    draw.rectangle([width // 3, height // 3, 2 * width // 3, 2 * height // 3], outline=(255, 106, 19), width=8)
    draw.text((20, 20), f"frame {seed}", fill=(255, 255, 255))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


def nef_with_preview(jpeg: bytes, orientation: int = 1, size: int = 0) -> bytes:
    """A small TIFF shaped like a NEF: orientation and exposure in its records,
    and a real JPEG preview in a sub-IFD."""
    entries0 = 4  # model, orientation, sub-IFDs, EXIF
    ifd0 = 8
    ifd0_len = 2 + entries0 * 12 + 4
    model_at = ifd0 + ifd0_len
    model = b"NIKON D850\x00\x00"
    sub = model_at + len(model)
    sub_len = 2 + 2 * 12 + 4
    exif = sub + sub_len
    exif_entries = 6
    exif_len = 2 + exif_entries * 12 + 4
    values = exif + exif_len  # rationals and the date
    date = b"2026:10:04 14:07:33\x00"
    rationals = struct.pack("<IIIIII", 1, 2000, 56, 10, 5000, 10)  # 1/2000 s, f/5.6, 500 mm
    jpeg_at = values + len(rationals) + len(date)

    out = bytearray(b"II*\x00" + struct.pack("<I", ifd0))
    out += struct.pack("<H", entries0)
    out += struct.pack("<HHII", 0x0110, 2, len(model), model_at)
    out += struct.pack("<HHIHH", 0x0112, 3, 1, orientation, 0)
    out += struct.pack("<HHII", 0x014A, 4, 1, sub)
    out += struct.pack("<HHII", 0x8769, 4, 1, exif)
    out += struct.pack("<I", 0)
    out += model
    out += struct.pack("<H", 2)
    out += struct.pack("<HHII", 0x0201, 4, 1, jpeg_at)
    out += struct.pack("<HHII", 0x0202, 4, 1, len(jpeg))
    out += struct.pack("<I", 0)
    out += struct.pack("<H", exif_entries)
    out += struct.pack("<HHII", 0x829A, 5, 1, values)
    out += struct.pack("<HHII", 0x829D, 5, 1, values + 8)
    out += struct.pack("<HHIHH", 0x8827, 3, 1, 400, 0)
    out += struct.pack("<HHII", 0x9003, 2, len(date), values + 24)
    out += struct.pack("<HHII", 0x920A, 5, 1, values + 16)
    out += struct.pack("<HHII", 0xA434, 2, 4, int.from_bytes(b"lens", "little"))
    out += struct.pack("<I", 0)
    out += rationals + date
    assert len(out) == jpeg_at
    out += jpeg
    if size > len(out):
        out += bytes(size - len(out))
    return bytes(out)


def make_photo_card(root: Path, count: int = 4, start: int = 20, folder: str = "DCIM/100ND850",
                    base_time: int = 1_791_700_000, width: int = 1200, height: int = 800, size: int = 0,
                    portrait: tuple = ()) -> list[Path]:
    """A card of NEF-shaped files with real previews, for the viewer."""
    target = Path(root) / folder
    target.mkdir(parents=True, exist_ok=True)
    made = []
    for i in range(count):
        path = target / f"N81_{start + i:04d}.NEF"
        path.write_bytes(nef_with_preview(jpeg_bytes(width, height, seed=start + i),
                                          orientation=6 if i in portrait else 1, size=size))
        os.utime(path, (base_time + i * 2, base_time + i * 2))
        made.append(path)
    return made
