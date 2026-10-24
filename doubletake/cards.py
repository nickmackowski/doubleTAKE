"""Finding the card that holds the second copy.

A card is erased only when the user prepares it for DoubleTake or formats it, each
after confirming (see the helper's prepare-drive and format-card). Otherwise the app
writes only inside its own folder on the card and leaves everything else there alone.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .util import human_bytes, iso_time

# Where the desktop opens cards and drives that are plugged in.
MEDIA_ROOTS = ("/run/media/", "/media/")
# Discs and system mounts are never a second copy.
SKIP_FS = {"iso9660", "udf", "squashfs", "tmpfs", "devtmpfs", "overlay", "proc", "sysfs"}
# The mark on a second-copy card, inside DoubleTake's folder on it.
MARKER = Path(".doubletake") / "card.json"
# Before 0.9.5 the folder was FieldPhotos and the mark .fieldstation/card.json. A card like that
# is moved over by renaming the two folders the first time it is seen (upgrade_layout).
FOLDER = "DoubleTake"
OLD_FOLDER = "FieldPhotos"
OLD_RECORDS = ".fieldstation"


@dataclass
class Card:
    mount: Path
    folder: str = FOLDER
    device: str = ""
    fstype: str = ""
    label: str = ""
    fs_uuid: str = ""
    read_only: bool = False
    card_id: str = ""  # set once the card has been tagged for use
    total: int = 0
    free: int = 0

    @property
    def root(self) -> Path:
        """The app's own folder on the card. Nothing is written outside it."""
        return self.mount / self.folder

    @property
    def key(self) -> str:
        """Identifies a card without writing to it, to remember "don't use this one"."""
        if self.fs_uuid:
            return f"uuid:{self.fs_uuid}"
        return f"path:{self.mount}:{self.total}"

    @property
    def is_mmc(self) -> bool:
        return os.path.basename(self.device).startswith("mmcblk")

    @property
    def noun(self) -> str:
        if self.is_mmc:
            return "microSD card"
        if self.is_usb:
            return "USB drive"
        return "drive" if self.device.startswith("/dev/") else "folder"

    @property
    def is_usb(self) -> bool:
        """A drive plugged into a USB port (the desktop opens these as /dev/sd...)."""
        return os.path.basename(self.device).startswith("sd")

    @property
    def style(self) -> str:
        """How the screens draw it: 'card' (the tablet's own slot), 'drive' or 'stick'."""
        if self.is_mmc:
            return "card"
        return usb_style(self.device) if self.is_usb else "drive"

    def refresh(self) -> None:
        try:
            usage = shutil.disk_usage(self.mount)
            self.total, self.free = usage.total, usage.free
        except OSError:
            pass

    def describe(self) -> str:
        fs = f", {self.fstype}" if self.fstype else ""
        return f"'{self.label}' ({human_bytes(self.total)}, {human_bytes(self.free)} free{fs})"


@dataclass
class Detection:
    # 'off', 'none', 'ready', 'new', 'declined', 'several' or 'readonly'
    state: str
    card: Card | None = None
    all: list = field(default_factory=list)  # every card seen, whatever its state
    unopened: list = field(default_factory=list)  # devices present but not opened by the system
    wanted: str = ""  # the folder named in the settings, when it is missing
    kind: str = ""  # "microsd" or "usb": the kind of drive the settings ask for
    other_kind: int = 0  # drives of the other kind that are plugged in, and so not used


# ----- reading the system's list of opened drives ---------------------------------


def _unescape(text: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def parse_mountinfo(text: str) -> list[dict]:
    """Parse /proc/self/mountinfo into mount point, type, device and options."""
    mounts = []
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        a, b = left.split(), right.split()
        if not sep or len(a) < 6 or len(b) < 2:
            continue
        options = set(a[5].split(","))
        if len(b) > 2:
            options |= set(b[2].split(","))
        mounts.append({
            "mount": _unescape(a[4]),
            "fstype": b[0],
            "source": _unescape(b[1]),
            "options": options,
        })
    return mounts


def _read_mountinfo() -> str:
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _fs_uuid(device: str) -> str:
    if not device.startswith("/dev/"):
        return ""
    try:
        real = os.path.realpath(device)
        for link in Path("/dev/disk/by-uuid").iterdir():
            if os.path.realpath(link) == real:
                return link.name
    except OSError:
        pass
    return ""


def read_marker(card: Card) -> str:
    for marker in (card.root / MARKER, card.root / OLD_RECORDS / MARKER.name,
                   card.mount / OLD_FOLDER / OLD_RECORDS / MARKER.name):
        try:
            with open(marker, encoding="utf-8") as fh:
                data = json.load(fh)
            return str(data.get("card_id") or "")
        except (OSError, ValueError, AttributeError):
            continue
    return ""


def upgrade_layout(mount: Path, folder: str = FOLDER) -> list:
    """A card prepared before 0.9.5: FieldPhotos becomes DoubleTake, .fieldstation becomes
    .doubletake. Renames only, on the card itself, so nothing is copied. Returns what moved."""
    moved = []
    old = mount / OLD_FOLDER
    try:
        if (folder == FOLDER and (old / OLD_RECORDS / MARKER.name).is_file()
                and not (mount / FOLDER).exists()):
            os.rename(old, mount / FOLDER)
            moved.append(f"{OLD_FOLDER} -> {FOLDER}")
        root = mount / folder
        if (root / OLD_RECORDS).is_dir() and not (root / MARKER.parent).exists():
            os.rename(root / OLD_RECORDS, root / MARKER.parent)
            moved.append(f"{OLD_RECORDS} -> {MARKER.parent}")
    except OSError:
        pass  # left as it is; read_marker still finds the old mark
    return moved


def card_at(path, folder: str = FOLDER, device: str = "", fstype: str = "",
            options: set | None = None) -> Card:
    path = Path(path)
    card = Card(mount=path, folder=folder, device=device, fstype=fstype, label=path.name or str(path))
    card.read_only = "ro" in (options or set()) or not os.access(path, os.W_OK)
    if not card.read_only:
        upgrade_layout(path, folder)
    card.fs_uuid = _fs_uuid(device)
    card.refresh()
    card.card_id = read_marker(card)
    return card


def candidates(folder: str, library, mountinfo: str | None = None, roots=MEDIA_ROOTS) -> list[Card]:
    """Cards and drives the desktop has opened, leaving out the tablet's own drive."""
    text = _read_mountinfo() if mountinfo is None else mountinfo
    try:
        library_dev = os.stat(library).st_dev if library else None
    except OSError:
        library_dev = None
    found, seen = [], set()
    for m in parse_mountinfo(text):
        mount = m["mount"]
        if mount in seen or m["fstype"] in SKIP_FS:
            continue
        if not any(mount.startswith(root) and len(mount) > len(root) for root in roots):
            continue
        try:
            if not os.path.isdir(mount) or os.stat(mount).st_dev == library_dev:
                continue
        except OSError:
            continue
        seen.add(mount)
        if drive_camera(mount, m["source"]):
            continue  # a camera that plugs in as a drive: read from, never written to
        found.append(card_at(mount, folder, m["source"], m["fstype"], m["options"]))
    return found


def drive_camera(mount, device: str) -> bool:
    from .sources import drive

    return drive.is_camera(mount, device, os.path.basename(str(mount)))


OPENABLE_FS = {"vfat", "exfat", "ntfs", "ext4", "ext3", "ext2", "btrfs", "xfs", "hfsplus", "apfs"}


def removable_to_open(lsblk_text: str) -> list:
    """Partitions on removable or plugged-in drives that are not opened yet, from lsblk -P."""
    out = []
    for line in lsblk_text.splitlines():
        row = dict(re.findall(r'(\w+)="([^"]*)"', line))
        if row.get("TYPE") not in ("part", "disk") or row.get("MOUNTPOINT"):
            continue
        if row.get("FSTYPE", "").lower() not in OPENABLE_FS:
            continue
        if row.get("RM") != "1" and row.get("HOTPLUG") != "1":
            continue  # the tablet's own drive
        name = row.get("NAME", "")
        if re.fullmatch(r"/dev/(mmcblk\d+p\d+|mmcblk\d+|sd[a-z]+\d*)", name):
            out.append(name)
    return out


def open_removable(run=None) -> list:
    """Open cards and drives that are plugged in, as the desktop would.

    The kiosk session has no desktop to do it, so the engine asks the system to, the
    same way the desktop does. Returns the devices opened.
    """
    import subprocess

    def default(args):
        return subprocess.run(args, capture_output=True, text=True, timeout=30)

    run = run or default
    try:
        listing = run(["lsblk", "-P", "-p", "-o", "NAME,TYPE,FSTYPE,MOUNTPOINT,RM,HOTPLUG"])
    except (OSError, subprocess.SubprocessError):
        return []
    opened = []
    for device in removable_to_open(listing.stdout or ""):
        try:
            done = run(["udisksctl", "mount", "--no-user-interaction", "-b", device])
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            opened.append(device)
    return opened


def unopened_devices(mountinfo: str | None = None, sys_block: str = "/sys/block", kind: str = "") -> list[str]:
    """Cards that are in a slot but that the system has not opened.

    Usually a card in a format this system cannot read.
    """
    text = _read_mountinfo() if mountinfo is None else mountinfo
    sources = [m["source"] for m in parse_mountinfo(text)]
    out = []
    try:
        entries = sorted(Path(sys_block).iterdir())
    except OSError:
        return out
    for dev in entries:
        name = dev.name
        try:
            is_card = re.fullmatch(r"mmcblk\d+", name) is not None
            if (kind == "usb" and is_card) or (kind == "microsd" and not is_card):
                continue
            if not is_card and name.startswith("sd"):
                is_card = (dev / "removable").read_text().strip() == "1"
            if not is_card:
                continue
            size = int((dev / "size").read_text().strip()) * 512
        except (OSError, ValueError):
            continue
        if size <= 0 or any(s.startswith(f"/dev/{name}") for s in sources):
            continue
        out.append(f"{name}, {human_bytes(size)}")
    return out


# ----- choosing the card ----------------------------------------------------------


def detect(cfg: dict, ledger, mountinfo: str | None = None) -> Detection:
    folder = cfg.get("second_copy_folder") or FOLDER
    setting = os.environ.get("DOUBLETAKE_CARD") or cfg.get("second_copy", "auto")
    if setting == "off":
        return Detection("off")
    if setting != "auto":
        path = Path(os.path.expanduser(str(setting)))
        if not path.is_dir():
            return Detection("none", wanted=str(path))
        cards = [card_at(path, folder)]
        kind, other = "", 0
    else:
        # The second copy lives on the kind of drive chosen in the settings, and only there.
        kind = "usb" if cfg.get("second_copy_device") == "usb" else "microsd"
        seen = candidates(folder, cfg["library"], mountinfo)
        cards = [c for c in seen if (c.is_usb if kind == "usb" else c.is_mmc)]
        other = len([c for c in seen if (c.is_mmc if kind == "usb" else c.is_usb)])

    cards = one_per_disk(cards)
    usable = [c for c in cards if ledger.card_state(c.key) != "declined"]
    if not usable:
        if cards:
            return Detection("declined", all=cards, kind=kind, other_kind=other)
        unopened = unopened_devices(mountinfo, kind=kind) if setting == "auto" else []
        return Detection("none", all=cards, unopened=unopened, kind=kind, other_kind=other)

    # A card already tagged for this job comes first, then a card in the
    # tablet's own slot, then anything else that is plugged in.
    tiers = [
        [c for c in usable if c.card_id],
        [c for c in usable if not c.card_id and c.is_mmc],
        [c for c in usable if not c.card_id and not c.is_mmc],
    ]
    tier = next(t for t in tiers if t)
    if len(tier) > 1:
        return Detection("several", all=cards, kind=kind, other_kind=other)
    card = tier[0]
    if card.read_only:
        return Detection("readonly", card=card, all=cards, kind=kind, other_kind=other)
    return Detection("ready" if card.card_id else "new", card=card, all=cards, kind=kind, other_kind=other)


def tag(card: Card, ledger) -> None:
    """Mark a card as the second copy, so it is recognised next time.

    Creates the app's folder on the card and one small file inside it.
    Raises OSError when the card cannot be written to.
    """
    marker = card.root / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    card_id = uuid.uuid4().hex[:16]
    data = {
        "format": 1,
        "card_id": card_id,
        "tagged": iso_time(time.time()),
        "about": "Second copy of photo shoots, written by DoubleTake. "
                 "Only this folder belongs to it.",
    }
    tmp = marker.with_name("card.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, marker)
    card.card_id = card_id
    ledger.clear_card(card.key)
    ledger.set_card(f"id:{card_id}", "used", card.label)


def decline(card: Card, ledger) -> None:
    """Remember not to use this card. Nothing is written to the card."""
    ledger.set_card(card.key, "declined", card.label)


def allow(card: Card, ledger) -> None:
    ledger.clear_card(card.key)


# ----- a thumb drive or an external drive? ------------------------------------------------
#
# Only for the icon. Linux cannot ask a USB drive what it is, so this goes by clues: an
# external hard drive or SSD usually talks the faster UAS protocol and says it is not
# removable; a thumb drive or a card reader usually uses the basic one and says it is.
# A drive of more than 1 TB is taken to be a drive. A wrong guess changes only the picture.


def _disk_name(device: str) -> str:
    name = os.path.basename(device)
    return re.sub(r"\d+$", "", name) if name.startswith("sd") else name


def usb_style(device: str, sys_block: str = "/sys/block") -> str:
    base = Path(sys_block) / _disk_name(device)
    try:
        if (base / "removable").read_text().strip() == "0":
            return "drive"
    except OSError:
        pass
    try:
        if int((base / "size").read_text().strip()) * 512 > 1_000_000_000_000:
            return "drive"
    except (OSError, ValueError):
        pass
    try:
        here = (base / "device").resolve()
    except OSError:
        return "stick"
    for _ in range(8):  # up to the USB interface, whose driver says which protocol
        driver = here / "driver"
        if driver.exists():
            name = os.path.basename(os.path.realpath(driver))
            if name == "uas":
                return "drive"
            if name == "usb-storage":
                return "stick"
        if here.parent == here:
            break
        here = here.parent
    return "stick"


def disk_of(device: str) -> str:
    """/dev/sdb1 -> /dev/sdb, /dev/mmcblk0p1 -> /dev/mmcblk0."""
    name = os.path.basename(device)
    if name.startswith("mmcblk"):
        name = re.sub(r"p[0-9]+$", "", name)
    elif name.startswith("sd"):
        name = re.sub(r"[0-9]+$", "", name)
    return "/dev/" + name if device.startswith("/dev/") else device


def one_per_disk(found: list) -> list:
    """One drive is one drive: of its partitions, the one already DoubleTake's, else the largest
    that can be written to. A drive formatted on a Mac or by Windows often has a small hidden
    partition beside the main one, which made one drive look like two."""
    best = {}
    order = []
    for c in found:
        key = disk_of(c.device) if c.device.startswith("/dev/") else str(c.mount)
        if key not in best:
            order.append(key)
            best[key] = c
            continue
        have = best[key]
        rank = lambda x: (bool(x.card_id), not x.read_only, x.total)
        if rank(c) > rank(have):
            best[key] = c
    return [best[k] for k in order]
