"""Copying exported JPEGs to a USB stick, checked, and ejecting it.

Only exports go to a stick: the second copy of the RAW files stays on the microSD
card. Like everything else here, each file is written under a temporary name, read
back from the stick and compared before it counts as copied. Nothing already on the
stick is removed.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from . import cards

FOLDER = "DoubleTake Exports"


def drives(cfg: dict) -> list:
    """USB sticks and drives the desktop has opened, leaving out the second-copy card."""
    found = []
    for card in cards.candidates(cfg.get("second_copy_folder", "FieldPhotos"), cfg.get("library")):
        if card.is_mmc or card.card_id or card.read_only:
            continue  # the microSD slot, or a card already used as the second copy
        found.append({"mount": str(card.mount), "label": card.label, "device": card.device,
                      "free": card.free, "total": card.total})
    return found


def _sha(path: Path, drop_cache: bool = False) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        if drop_cache and hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fh.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def copy(files: list, mount: str, progress=None) -> dict:
    """Copy [(shoot, name, path)] to <stick>/DoubleTake Exports/<shoot>/<name>, each read back."""
    root = Path(mount)
    if not root.is_dir() or not os.access(root, os.W_OK):
        raise OSError("The USB stick is not there, or cannot be written to.")
    need = sum(Path(p).stat().st_size for _, _, p in files)
    st = os.statvfs(root)
    if st.f_bavail * st.f_frsize < need + 1024 * 1024:
        raise OSError("There is not enough room on the USB stick.")
    copied, problems = 0, []
    for i, (shoot, name, path) in enumerate(files):
        if progress:
            progress(i, name)
        target = root / FOLDER / shoot / name
        tmp = target.with_name(f".{name}.part")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            want = _sha(Path(path))
            with open(path, "rb") as src, open(tmp, "wb") as dst:
                for block in iter(lambda: src.read(1024 * 1024), b""):
                    dst.write(block)
                dst.flush()
                os.fsync(dst.fileno())
            if _sha(tmp, drop_cache=True) != want:
                raise OSError("the copy on the stick did not match")
            os.replace(tmp, target)
            copied += 1
        except OSError as exc:
            problems.append(f"{name}: {exc}")
            try:
                tmp.unlink()
            except OSError:
                pass
    try:
        os.sync()
    except AttributeError:
        pass
    return {"copied": copied, "problems": problems}


def _whole_disk(device: str) -> str:
    """/dev/sdb1 -> /dev/sdb, so the stick can be powered off once unmounted."""
    match = re.match(r"^(/dev/sd[a-z]+)\d*$", device)
    return match.group(1) if match else ""


def eject(mount: str, device: str) -> str:
    """Unmount the stick and power it off. Returns an error to show, or ''."""
    if not device.startswith("/dev/"):
        return "Cannot tell which drive this is. Eject it from the desktop instead."
    try:
        done = subprocess.run(["udisksctl", "unmount", "-b", device], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Could not eject it: {exc}"
    if done.returncode != 0:
        if "busy" in done.stderr.lower():
            return "The USB stick is still in use. Close anything using it and try again."
        return "Could not eject it: " + (done.stderr.strip().splitlines() or ["unknown error"])[-1]
    disk = _whole_disk(device)
    if disk:
        subprocess.run(["udisksctl", "power-off", "-b", disk], capture_output=True, text=True, timeout=30)
    return ""
