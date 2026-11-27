"""The watermark on photos sent to a phone.

Only the copies a phone is given are marked: the photos on the card, Copy 1, Copy 2, the
NAS and the exported JPEGs themselves are never changed.

The mark is one or two lines of thin white text, about 78% opaque with a faint shadow,
running from the bottom-left corner toward the top-right one, so its slope follows the
photo's own shape. It is sized from the photo, so a tall photo and a wide one look alike.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
from pathlib import Path

FONT = Path(__file__).resolve().parent / "assets" / "plex-sans-condensed-300.ttf"
MAX_LINE = 60
OPACITY = 0.78
SHADOW = 0.28
SECOND = 0.62  # the second line's size, against the first


def clean_lines(lines) -> list:
    """The lines as kept: at most two, trimmed, empty ones dropped."""
    out = []
    for line in list(lines or [])[:2]:
        text = " ".join(str(line or "").split())[:MAX_LINE]
        if text:
            out.append(text)
    return out


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(str(FONT), size)
    except OSError:
        return ImageFont.load_default()


def stamp(img, lines):
    """The picture with the mark on it, as a new RGB picture. No lines: a plain copy."""
    from PIL import Image, ImageDraw, ImageFilter

    lines = clean_lines(lines)
    base = img.convert("RGB")
    if not lines:
        return base.copy()
    w, h = base.size
    diagonal = math.hypot(w, h)
    angle = math.degrees(math.atan2(h, w))  # bottom-left to top-right

    # Size the first line so the longest line spans about 62% of the diagonal, and never
    # taller than a tenth of the short side.
    probe = 200
    widths = []
    for i, text in enumerate(lines):
        f = _font(int(probe * (SECOND if i else 1)))
        left, _, right, _ = f.getbbox(text)
        widths.append(max(1, right - left))
    size = probe * (0.62 * diagonal) / max(widths)
    size = max(10, min(size, 0.10 * min(w, h) / 0.75))

    fonts = [_font(max(8, int(size * (SECOND if i else 1)))) for i in range(len(lines))]
    boxes = [f.getbbox(t) for f, t in zip(fonts, lines)]
    gap = int(size * 0.18)
    block_w = max(b[2] - b[0] for b in boxes)
    block_h = sum(b[3] - b[1] for b in boxes) + gap * (len(lines) - 1)
    pad = int(size * 0.5)
    layer = Image.new("L", (block_w + 2 * pad, block_h + 2 * pad), 0)
    draw = ImageDraw.Draw(layer)
    y = pad
    for f, t, b in zip(fonts, lines, boxes):
        lw = b[2] - b[0]
        draw.text((pad + (block_w - lw) // 2 - b[0], y - b[1]), t, font=f, fill=255)
        y += (b[3] - b[1]) + gap

    text = layer.rotate(angle, resample=Image.BICUBIC, expand=True)
    shadow = text.filter(ImageFilter.GaussianBlur(max(1.0, size * 0.04)))
    tw, th = text.size
    x, y = (w - tw) // 2, (h - th) // 2
    offset = max(1, int(size * 0.03))

    out = base.copy()
    black = Image.new("RGB", text.size, (0, 0, 0))
    white = Image.new("RGB", text.size, (255, 255, 255))
    out.paste(black, (x + offset, y + offset), shadow.point(lambda v: int(v * SHADOW)))
    out.paste(white, (x, y), text.point(lambda v: int(v * OPACITY)))
    return out


def marked_jpeg(path: Path, lines, longest: int | None = None, quality: int = 92) -> bytes | None:
    """A photo as JPEG bytes with the mark on it, upright, optionally made smaller.
    Keeps the photo's EXIF (without the turn, which is now applied). None when unreadable."""
    try:
        from PIL import Image, ImageOps

        Image.MAX_IMAGE_PIXELS = 120_000_000
        with Image.open(path) as src:
            exif = src.getexif()
            img = ImageOps.exif_transpose(src)
            if longest:
                img.thumbnail((longest, longest))
            out = stamp(img, lines)
        if 0x0112 in exif:
            del exif[0x0112]
        buf = io.BytesIO()
        kw = {"quality": quality}
        if not longest:
            kw["exif"] = exif.tobytes()
        out.save(buf, "JPEG", **kw)
        return buf.getvalue()
    except Exception:
        return None


class Cache:
    """Marked full-size photos, kept on disk while a share runs so a photo is marked once."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def key(self, path: Path, lines) -> str:
        try:
            st = Path(path).stat()
            mark = f"{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            mark = "?"
        text = "\n".join([str(path), mark] + clean_lines(lines))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

    def get(self, path: Path, lines) -> bytes | None:
        target = self.folder / (self.key(path, lines) + ".jpg")
        try:
            return target.read_bytes()
        except OSError:
            pass
        body = marked_jpeg(path, lines)
        if body is None:
            return None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".part")
            tmp.write_bytes(body)
            os.replace(tmp, target)
        except OSError:
            pass
        return body

    def clear(self) -> None:
        try:
            for item in self.folder.iterdir():
                if item.suffix in (".jpg", ".part"):
                    item.unlink(missing_ok=True)
        except OSError:
            pass


def sample(lines, shape=(1800, 1200)) -> bytes:
    """A preview of the mark on a plain dark-grey picture, as JPEG bytes."""
    from PIL import Image

    w, h = shape
    img = Image.new("RGB", (w, h), (0, 0, 0))
    # A soft gradient so the shadow and the opacity can be judged.
    top, bottom = (92, 104, 118), (38, 42, 48)
    grad = Image.linear_gradient("L").resize((w, h))
    img = Image.composite(Image.new("RGB", (w, h), bottom), Image.new("RGB", (w, h), top), grad)
    buf = io.BytesIO()
    stamp(img, lines).save(buf, "JPEG", quality=88)
    return buf.getvalue()
