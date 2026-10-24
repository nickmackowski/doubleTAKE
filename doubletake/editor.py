"""Quick edits in the field, and JPEG exports for sharing.

The edits work on the full-size JPEG preview the camera stores inside each RAW file,
so they are quick on a tablet and look like the camera's own JPEG. Real editing is
done later, from the RAW file, on the editing workstation.

Nothing here changes a photo. The edits are a small settings file per photo in the
records folder, and an export is a new JPEG in the library's Exports folder, kept
apart from the shoots.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import threading
import time
from pathlib import Path

from . import photos

EXPORTS = "Exports"

# Every adjustment, its neutral value and its range.
SLIDERS = {
    "angle": (0.0, -15.0, 15.0),       # straighten, degrees, positive turns clockwise
    "exposure": (0.0, -3.0, 3.0),      # stops
    "contrast": (0, -100, 100),
    "highlights": (0, -100, 100),
    "shadows": (0, -100, 100),
    "temp": (0, -100, 100),            # warmer is positive
    "tint": (0, -100, 100),            # magenta is positive
    "saturation": (0, -100, 100),
    "sharpen": (0, 0, 100),
}
ASPECTS = {"free": None, "original": None, "3:2": 3 / 2, "4:5": 4 / 5, "1:1": 1.0, "16:9": 16 / 9}
SIZES = {"full": None, "2048": 2048, "1080": 1080}
QUALITIES = {"high": 92, "standard": 85}

_render_lock = threading.BoundedSemaphore(2)


def neutral() -> dict:
    out = {key: value[0] for key, value in SLIDERS.items()}
    out.update(crop=None, aspect="free", bw=False)
    return out


def clean(params) -> dict:
    """The edits, checked: unknown keys dropped, numbers kept in range."""
    out = neutral()
    if not isinstance(params, dict):
        return out
    for key, (default, low, high) in SLIDERS.items():
        value = params.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        value = min(high, max(low, value))
        out[key] = round(float(value), 2) if isinstance(default, float) else int(round(value))
    if params.get("aspect") in ASPECTS:
        out["aspect"] = params["aspect"]
    out["bw"] = params.get("bw") is True
    box = params.get("crop")
    if isinstance(box, (list, tuple)) and len(box) == 4 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in box):
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in box)
        if x1 - x0 >= 0.02 and y1 - y0 >= 0.02 and (x0, y0, x1, y1) != (0.0, 0.0, 1.0, 1.0):
            out["crop"] = [round(x0, 5), round(y0, 5), round(x1, 5), round(y1, 5)]
    return out


def is_edited(params: dict) -> bool:
    return clean(params) != neutral()


# ----- where edits and exports live ----------------------------------------------------


def edits_path(data_dir, file_id: int) -> Path:
    return Path(data_dir) / "edits" / f"{int(file_id)}.json"


def load(data_dir, file_id: int) -> dict:
    try:
        return clean(json.loads(edits_path(data_dir, file_id).read_text()))
    except (OSError, ValueError):
        return neutral()


def save(data_dir, file_id: int, params) -> dict:
    """Keep the edits for one photo. Neutral edits remove the file."""
    params = clean(params)
    path = edits_path(data_dir, file_id)
    if not is_edited(params):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return params
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(params))
    os.replace(tmp, path)
    return params


def forget(data_dir, file_id: int) -> None:
    try:
        edits_path(data_dir, file_id).unlink()
    except OSError:
        pass


def edited_ids(data_dir) -> set:
    folder = Path(data_dir) / "edits"
    try:
        return {int(p.stem) for p in folder.glob("*.json") if p.stem.isdigit()}
    except OSError:
        return set()


def exports_dir(library) -> Path:
    return Path(library) / EXPORTS


def export_path(library, shoot_folder: str, photo_name: str) -> Path:
    """Exports/<shoot>/<photo>.jpg, with the shoot folder's own name (not its year folder)."""
    leaf = Path(shoot_folder).name
    return exports_dir(library) / leaf / (Path(photo_name).stem + ".jpg")


_SAFE = re.compile(r"^[^/\\\x00]+$")


def list_exports(library) -> list:
    """[{shoot, files: [{name, size, time}]}], newest shoot first."""
    root = exports_dir(library)
    out = []
    try:
        folders = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except OSError:
        return out
    for folder in folders:
        files = []
        for f in folder.iterdir():
            if f.is_file() and f.suffix.lower() == ".jpg" and not f.name.startswith("."):
                st = f.stat()
                files.append({"name": f.name, "size": st.st_size, "time": st.st_mtime})
        if files:
            files.sort(key=lambda x: x["name"])
            out.append({"shoot": folder.name, "files": files, "time": max(x["time"] for x in files)})
    out.sort(key=lambda s: s["time"], reverse=True)
    return out


def export_file(library, shoot: str, name: str):
    """The path of one export, only if it is really inside the Exports folder."""
    if not (_SAFE.match(shoot or "") and _SAFE.match(name or "")) or shoot in (".", "..") or name in (".", ".."):
        return None
    root = exports_dir(library).resolve()
    path = (root / shoot / name).resolve()
    if root not in path.parents or not path.is_file() or path.suffix.lower() != ".jpg":
        return None
    return path


# ----- the adjustments -------------------------------------------------------------------


def _to_linear(v: float) -> float:
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _to_srgb(v: float) -> float:
    v = max(0.0, v)
    return 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055


def tone_tables(p: dict) -> list:
    """One look-up table per colour channel, for white balance, exposure and tones."""
    t, u = p["temp"] / 100.0, p["tint"] / 100.0
    gains = (1 + 0.22 * t, 1 - 0.18 * u, 1 - 0.22 * t)  # warmer: more red, less blue
    weight = 0.2126 * gains[0] + 0.7152 * gains[1] + 0.0722 * gains[2]
    gains = tuple(g / weight for g in gains)  # the same brightness overall
    stops = 2 ** p["exposure"]
    c, hi, sh = p["contrast"] / 100.0, p["highlights"] / 100.0, p["shadows"] / 100.0
    tables = []
    for gain in gains:
        table = []
        for i in range(256):
            y = _to_linear(i / 255.0) * gain * stops
            if y > 0.85:  # roll off instead of clipping hard when brightened
                y = 0.85 + (1 - math.exp(-(y - 0.85) / 0.15)) * 0.15
            y = _to_srgb(y)
            if c >= 0:
                y += c * ((0.5 - math.cos(math.pi * y) / 2) - y)
            else:
                y = 0.5 + (y - 0.5) * (1 + c * 0.5)
            y += sh * 0.35 * (y * (1 - y) ** 2 * 27 / 4)    # strongest at a third
            y += hi * 0.35 * (y ** 2 * (1 - y) * 27 / 4)    # strongest at two thirds
            table.append(int(round(min(1.0, max(0.0, y)) * 255)))
        tables.extend(table)
    return tables


def straighten(img, angle: float):
    """Turn the picture level, and trim it so no empty corners show."""
    if abs(angle) < 0.01:
        return img
    from PIL import Image

    w, h = img.size
    a = math.radians(abs(angle))
    scale = max((w * math.cos(a) + h * math.sin(a)) / w, (w * math.sin(a) + h * math.cos(a)) / h)
    turned = img.rotate(-angle, resample=Image.BICUBIC, expand=True)
    cw, ch = w / scale, h / scale
    cx, cy = turned.size[0] / 2, turned.size[1] / 2
    return turned.crop((round(cx - cw / 2), round(cy - ch / 2), round(cx + cw / 2), round(cy + ch / 2)))


def apply(img, p: dict, crop: bool = True, size: int | None = None):
    """The edits applied to a picture. 'size' shrinks the long side, before sharpening."""
    from PIL import Image, ImageEnhance, ImageFilter

    p = clean(p)
    img = straighten(img.convert("RGB"), p["angle"])
    if crop and p["crop"]:
        w, h = img.size
        x0, y0, x1, y1 = p["crop"]
        img = img.crop((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)))
    if size and max(img.size) > size:
        img.thumbnail((size, size), Image.LANCZOS)
    if any(p[k] for k in ("exposure", "contrast", "highlights", "shadows", "temp", "tint")):
        img = img.point(tone_tables(p))
    if p["bw"]:
        img = img.convert("L").convert("RGB")
    elif p["saturation"]:
        img = ImageEnhance.Color(img).enhance(1 + p["saturation"] / 100.0)
    if p["sharpen"]:
        radius = max(0.6, max(img.size) / 2400)
        img = img.filter(ImageFilter.UnsharpMask(radius=radius, percent=int(p["sharpen"] * 1.6), threshold=2))
    return img


# ----- preview and export ----------------------------------------------------------------


def preview(source_jpeg: Path, p: dict, crop: bool = True, long_side: int = 1280) -> bytes | None:
    """The edited screen-size picture, for the editor's live view."""
    from PIL import Image

    with _render_lock:
        try:
            img = Image.open(source_jpeg)
            img.draft("RGB", (long_side, long_side))
            img = img.convert("RGB")
            img.thumbnail((long_side, long_side))
            out = io.BytesIO()
            apply(img, p, crop=crop).save(out, "JPEG", quality=85)
            return out.getvalue()
        except Exception:
            return None


def _exif(details: dict, width: int, height: int):
    from PIL import Image

    exif = Image.Exif()
    exif[0x0131] = "DoubleTake"
    exif[0x0112] = 1
    model = details.get("model", "").strip()
    if model:
        exif[0x0110] = model
        make = model.split(" ")[0]
        if make.isupper() and len(make) > 2:
            exif[0x010F] = make
    taken = details.get("taken", "")
    if re.match(r"^\d{4}:\d\d:\d\d \d\d:\d\d:\d\d", taken):
        exif[0x0132] = taken[:19]
    return exif, taken[:19] if taken else ""


def export(photo_path: Path, p: dict, target: Path, size: str = "full", quality: str = "high") -> dict:
    """Make the JPEG. Written under a temporary name, then renamed. Returns its details."""
    long_side = SIZES.get(size, None)
    q = QUALITIES.get(quality, 92)
    started = time.time()
    with _render_lock:
        img, orientation = photos._open(photo_path)
        if img is None:
            raise ValueError("This photo has no picture inside it to export.")
        img = photos._turn(img.convert("RGB"), orientation)
        img = apply(img, p, crop=True, size=long_side)
        details = photos.read_details(photo_path)
        exif, taken = _exif(details, *img.size)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{threading.get_ident()}.part")
        save_args = {"quality": q, "optimize": True, "progressive": True, "subsampling": 0 if q >= 90 else 2}
        try:
            img.save(tmp, "JPEG", exif=exif.tobytes(), **save_args)
        except Exception:
            img.save(tmp, "JPEG", **save_args)
        os.replace(tmp, target)
        if taken:
            try:
                when = time.mktime(time.strptime(taken, "%Y:%m:%d %H:%M:%S"))
                os.utime(target, (time.time(), when))  # sorts by when it was taken
            except (ValueError, OverflowError, OSError):
                pass
    return {"path": str(target), "width": img.size[0], "height": img.size[1], "bytes": target.stat().st_size,
            "seconds": round(time.time() - started, 1)}
