"""Sources the engine can copy from."""

from __future__ import annotations

import os
from pathlib import Path

from .base import NotSupported, Source, SourceError, SourceFile, SourceInfo


def open_source(spec: str) -> Source:
    """Build a source from a short description.

    "camera"        the camera plugged in over USB
    "dir:/path"     a folder
    "sim:/path"     a folder presented through the camera library, for testing
    """
    spec = (spec or "camera").strip()
    if spec == "camera":
        # A camera that speaks the camera protocol comes first; then one that is a drive.
        if not _camera_protocol_present():
            from . import drive

            found = drive.find()
            if found is not None:
                return drive.DriveCameraSource(found)
        from .camera import CameraSource

        return CameraSource()
    if spec.startswith("sim:"):
        from .camera import CameraSource

        return CameraSource(simulate_dir=spec[4:])
    if spec.startswith("dir:"):
        from .folder import FolderSource

        return FolderSource(spec[4:])
    raise SourceError(f"Unknown source '{spec}'. Use camera, dir:/path or sim:/path.")


def source_present(spec: str, usb_root: str = "/sys/bus/usb/devices"):
    """Is the source plugged in? A cheap check that does not open the camera.

    True, False, or None when it cannot be told.
    """
    spec = (spec or "camera").strip()
    if spec.startswith(("dir:", "sim:")):
        return os.path.isdir(os.path.expanduser(spec[4:]))
    present = _camera_protocol_present(usb_root)
    if present:
        return True
    try:
        from . import drive

        if drive.find() is not None:
            return True
    except Exception:
        pass
    return present


def _camera_protocol_present(usb_root: str = "/sys/bus/usb/devices"):
    """A camera on USB that speaks the still-image protocol. True, False, or None when unknown."""
    try:
        root = Path(usb_root)
        for vendor in root.glob("*/idVendor"):
            if vendor.read_text().strip().lower() == "04b0":  # Nikon
                return True
        for cls in root.glob("*/bInterfaceClass"):
            if cls.read_text().strip() == "06":  # any camera that speaks the still-image protocol
                return True
        return False
    except OSError:
        return None


__all__ = ["NotSupported", "Source", "SourceError", "SourceFile", "SourceInfo", "open_source", "source_present"]
