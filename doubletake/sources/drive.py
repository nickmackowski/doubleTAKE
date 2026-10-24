"""Camera cards that show up as a drive.

Two ways a camera's card arrives as a drive:
- a camera that plugs in as a drive (the Insta360 Ace Pro 2 in its U-Disk mode shows up
  as a drive holding DCIM/Camera01), and
- a camera's card in a card reader, or in the tablet's own card slot.

Either is read like a camera's card: only DCIM is looked at, nothing on it is ever
written, and it is never offered as the second copy, for exports, or to be prepared
(erased) for DoubleTake.

A card or drive counts as a camera's card when it has a DCIM folder and is not one of
DoubleTake's own cards (which carry DoubleTake's mark).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .base import SourceInfo
from .folder import FolderSource

# Makers whose cameras plug in as a drive. Matched against the drive's own maker and
# product names, and its label, ignoring case.
DRIVE_CAMERAS = ("insta360",)
MEDIA_ROOTS = ("/run/media/", "/media/")
# DoubleTake's own card: cards.MARKER in its folder, or the names used before 0.9.5.
OWN_MARKS = ("DoubleTake/.doubletake/card.json", "FieldPhotos/.fieldstation/card.json",
             "DoubleTake/.fieldstation/card.json", ".doubletake/card.json", ".fieldstation/card.json")
BY_UUID = "/dev/disk/by-uuid"


@dataclass
class DriveCamera:
    mount: Path
    device: str
    model: str
    serial: str
    reader: bool = False  # a camera's card in a reader or the card slot, not a camera


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return ""


def _disk(device: str) -> str:
    """/dev/sdb1 -> sdb"""
    name = os.path.basename(device)
    return re.sub(r"\d+$", "", name) if name.startswith("sd") else name


def describe(device: str, sys_class: str = "/sys/class/block") -> dict:
    """The maker, product and serial number a drive reports, from its USB and SCSI details."""
    out = {"vendor": "", "product": "", "serial": ""}
    base = Path(sys_class) / _disk(device)
    out["vendor"] = _read(base / "device" / "vendor")
    out["product"] = _read(base / "device" / "model")
    try:
        here = (base / "device").resolve()
    except OSError:
        return out
    for _ in range(8):  # up to the USB device, which knows its own names
        if (here / "idVendor").is_file():
            out["vendor"] = _read(here / "manufacturer") or out["vendor"]
            out["product"] = _read(here / "product") or out["product"]
            out["serial"] = _read(here / "serial")
            break
        if here.parent == here:
            break
        here = here.parent
    return out


def is_camera(mount, device: str, label: str = "", sys_class: str = "/sys/class/block") -> bool:
    """A camera's card: a card or USB drive with a DCIM folder that is not DoubleTake's own."""
    if not re.match(r"^/dev/(sd[a-z]+|mmcblk[0-9]+)", device or ""):
        return False
    root = Path(mount)
    try:
        return (root / "DCIM").is_dir() and not any((root / m).is_file() for m in OWN_MARKS)
    except OSError:
        return False


def is_maker_camera(device: str, label: str = "", sys_class: str = "/sys/class/block") -> bool:
    """The drive is a camera itself (it names a camera maker), not a card reader."""
    if not (device or "").startswith("/dev/sd"):
        return False
    names = describe(device, sys_class)
    text = " ".join([names["vendor"], names["product"], label]).lower()
    return any(maker in text for maker in DRIVE_CAMERAS)


def volume_id(device: str, by_uuid: str = BY_UUID) -> str:
    """The card's own volume ID (set when it was last formatted), or "" if not found."""
    try:
        for link in Path(by_uuid).iterdir():
            if os.path.realpath(link) == os.path.realpath(device):
                return link.name
    except OSError:
        pass
    return ""


def _card_model(label: str) -> str:
    """How a camera's card in a reader is named: by its label (cameras often set their
    model there, such as NIKON D850), else simply Camera card."""
    label = (label or "").strip()
    if label and label.upper() not in ("UNTITLED", "NO NAME", "NO_NAME", "DISK", "SD", "SD_CARD"):
        return f"Camera card ({label})"
    return "Camera card"


def _model(names: dict, label: str) -> str:
    product = re.sub(r"\s+", " ", names["product"]).strip()
    if "insta360" in product.lower():
        return product
    if product:
        return f"Insta360 {product}"
    return "Insta360 " + (label or "camera")


def find(mountinfo: str | None = None, sys_class: str = "/sys/class/block", by_uuid: str = BY_UUID) -> DriveCamera | None:
    """The camera plugged in as a drive, if there is one."""
    from ..cards import _read_mountinfo, parse_mountinfo

    text = _read_mountinfo() if mountinfo is None else mountinfo
    for m in parse_mountinfo(text):
        mount, device = m["mount"], m["source"]
        if not any(mount.startswith(root) for root in MEDIA_ROOTS):
            continue
        label = os.path.basename(mount)
        if not is_camera(mount, device, label, sys_class):
            continue
        if is_maker_camera(device, label, sys_class):
            names = describe(device, sys_class)
            serial = names["serial"] or label
            return DriveCamera(Path(mount), device, _model(names, label), "drive:" + serial)
        # A camera's card in a reader. Its identity is the card's own volume ID, not the
        # reader's, so two cards in the same reader are told apart.
        ident = volume_id(device, by_uuid) or f"{label}:{_size(mount)}"
        return DriveCamera(Path(mount), device, _card_model(label), "card:" + ident, reader=True)
    return None


def _size(mount) -> int:
    try:
        st = os.statvfs(mount)
        return st.f_blocks * st.f_frsize
    except OSError:
        return 0


class DriveCameraSource(FolderSource):
    """A camera that is a drive, read from its DCIM folder only."""

    def __init__(self, camera: DriveCamera):
        super().__init__(str(camera.mount))
        self.camera = camera

    def info(self) -> SourceInfo:
        if self.camera.reader:
            return SourceInfo(kind="card", model=self.camera.model, serial=self.camera.serial,
                              storage=[f"Camera card: {self.camera.mount.name}"])
        return SourceInfo(kind="camera", model=self.camera.model, serial=self.camera.serial,
                          storage=[f"Plugged in as a drive: {self.camera.mount.name}"])

    def list_files(self, progress=None):
        if not (self.root / "DCIM").is_dir():
            return []
        return super().list_files(progress)
