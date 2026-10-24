"""A camera over USB, through the gphoto2 library.

The same class can present a plain folder through the camera library
("simulated camera"), which exercises the library code path without a camera.
"""

from __future__ import annotations

import os
import re

from .base import NotSupported, Source, SourceError, SourceFile, SourceInfo



# What cameras call their makers, and what photographers call them.
_BRANDS = {
    "nikon": "Nikon", "canon": "Canon", "sony": "Sony", "fujifilm": "Fujifilm", "fuji": "Fujifilm",
    "panasonic": "Panasonic", "olympus": "Olympus", "om digital": "OM System", "om system": "OM System",
    "pentax": "Pentax", "ricoh": "Ricoh", "leica": "Leica", "hasselblad": "Hasselblad", "sigma": "Sigma",
    "phase one": "Phase One", "eastman kodak": "Kodak", "kodak": "Kodak", "samsung": "Samsung",
}


# Sony cameras report a model code, not the name on the camera. Most codes follow a
# pattern (see sony_name); these are the ones that do not, or read better spelled out.
_SONY = {
    "ILCE-7RM3A": "α7R IIIA", "ILCE-7RM4A": "α7R IVA", "ILCE-7CR": "α7CR",
    "ILCE-QX1": "QX1", "ILME-FX3": "FX3", "ILME-FX30": "FX30", "ILME-FX6": "FX6",
    "ZV-E10M2": "ZV-E10 II", "ZV-E10": "ZV-E10", "ZV-E1": "ZV-E1", "ZV-1": "ZV-1",
    "ZV-1M2": "ZV-1 II", "ZV-1F": "ZV-1F",
    "SLT-A99V": "α99", "SLT-A77V": "α77", "SLT-A77M2": "α77 II", "SLT-A58": "α58",
    "ILCA-99M2": "α99 II", "ILCA-77M2": "α77 II", "ILCA-68": "α68",
    "DSC-RX1RM2": "RX1R II", "DSC-RX1RM3": "RX1R III", "DSC-RX10M4": "RX10 IV",
}
_ROMAN = {"2": "II", "3": "III", "4": "IV", "5": "V", "6": "VI", "7": "VII", "8": "VIII"}


def sony_name(code: str) -> str:
    """'ILCE-7RM5' -> 'α7R V', 'ILCE-6700' -> 'α6700', 'DSC-RX100M7' -> 'RX100 VII'. Unknown codes stay as they are."""
    code = (code or "").strip()
    key = code.upper()
    if key in _SONY:
        return _SONY[key]
    # Interchangeable-lens bodies: ILCE-<number><letters>M<generation>
    match = re.fullmatch(r"ILCE-(\d+)([A-Z]*?)(?:M(\d))?", key)
    if match and (match.group(3) is None or match.group(3) in _ROMAN):
        number, letters, gen = match.groups()
        return "α" + number + letters + (" " + _ROMAN[gen] if gen else "")
    # Compact cameras: DSC-RX100M7, DSC-RX0M2, DSC-HX99
    match = re.fullmatch(r"DSC-([A-Z]+\d+[A-Z]*?)(?:M(\d))?", key)
    if match and (match.group(2) is None or match.group(2) in _ROMAN):
        name, gen = match.groups()
        return name + (" " + _ROMAN[gen] if gen else "")
    return code


def brand_of(manufacturer: str) -> str:
    """'Nikon Corporation' -> 'Nikon', 'Canon Inc.' -> 'Canon', 'FUJIFILM' -> 'Fujifilm'."""
    text = re.sub(r"\s+", " ", (manufacturer or "").strip())
    low = text.lower()
    for key, name in _BRANDS.items():
        if low.startswith(key):
            return name
    text = re.sub(r"[,.]?\s*(corporation|corp\.?|inc\.?|co\.?,? ?ltd\.?|ltd\.?|gmbh|ag|imaging.*|electronics.*)$", "",
                  text, flags=re.I).strip(" ,.")
    return text.title() if text.isupper() else text


def camera_name(manufacturer: str, model: str, listed: str = "") -> str:
    """The camera's name with its brand: 'Nikon D850', 'Canon EOS-1D Mark II'.

    'manufacturer' and 'model' are what the camera reports; 'listed' is libgphoto2's
    own name for it, such as 'Nikon DSC D850', used for the brand when the camera
    does not report one.
    """
    model = re.sub(r"\s+", " ", (model or "").strip())
    listed = re.sub(r"\s+", " ", (listed or "").strip())
    brand = brand_of(manufacturer) or brand_of(listed.split(" ")[0] if listed else "")
    if not model:
        model = listed.replace("Nikon DSC ", "Nikon ") if listed else ""
    if not model:
        return brand or "Camera"
    model = model.replace("Nikon DSC ", "Nikon ")
    if brand == "Sony" or (not brand and re.match(r"(ILCE|ILME|ILCA|SLT|DSC|ZV)-", model, re.I)):
        bare = re.sub(r"^sony\s+", "", model, flags=re.I)
        return "Sony " + sony_name(bare)
    if not brand:
        return model
    if model.lower().startswith(brand.lower() + " ") or model.lower() == brand.lower():
        return brand + model[len(brand):]  # already has the brand; use the tidy spelling ('SONY' -> 'Sony')
    return brand + " " + model


class CameraSource(Source):
    def __init__(self, simulate_dir: str | None = None):
        self.simulate_dir = os.path.abspath(os.path.expanduser(simulate_dir)) if simulate_dir else None
        self.gp = None
        self.cam = None
        self._config = None
        self._keep = None  # keeps the last downloaded file object alive while its bytes are in use
        # Assume a real camera can read in pieces until it tells us otherwise.
        self._chunks = simulate_dir is None

    @property
    def supports_chunks(self) -> bool:  # type: ignore[override]
        return self._chunks

    # ----- connection -----------------------------------------------------

    def open(self) -> None:
        try:
            import gphoto2 as gp
        except ImportError as exc:
            raise SourceError("The camera library is not installed. Run doubletake setup in a terminal first.") from exc
        self.gp = gp
        cam = gp.Camera()
        try:
            if self.simulate_dir:
                if not os.path.isdir(self.simulate_dir):
                    raise SourceError(f"Folder not found: {self.simulate_dir}")
                abilities = gp.CameraAbilitiesList()
                abilities.load()
                cam.set_abilities(abilities.get_abilities(abilities.lookup_model("Directory Browse")))
                ports = gp.PortInfoList()
                ports.load()
                cam.set_port_info(ports.get_info(ports.lookup_path("disk:" + self.simulate_dir)))
            cam.init()
        except gp.GPhoto2Error as exc:
            raise SourceError(self._explain(exc)) from exc
        self.cam = cam

    def close(self) -> None:
        if self.cam is not None:
            try:
                self.cam.exit()
            except Exception:
                pass
        self.cam = None
        self._config = None
        self._keep = None

    def _explain(self, exc) -> str:
        gp = self.gp
        code = getattr(exc, "code", None)
        if code == gp.GP_ERROR_MODEL_NOT_FOUND:
            return (
                "No camera found. Check the cable, switch the camera on, "
                "and half-press the shutter to wake it."
            )
        if code == gp.GP_ERROR_IO_USB_CLAIM:
            return (
                "The camera is connected, but another program has taken hold of it "
                "(usually the desktop's file manager). Eject the camera in Files, or run doubletake setup "
                "in a terminal to stop the desktop grabbing it, then retry."
            )
        if code == gp.GP_ERROR_CAMERA_BUSY:
            return "The camera is busy. Wait a moment and try again."
        if code in (gp.GP_ERROR_IO, gp.GP_ERROR_IO_USB_FIND, gp.GP_ERROR_IO_READ, gp.GP_ERROR_TIMEOUT):
            return "Lost contact with the camera. Check the cable and that the camera is still on."
        return f"Camera error: {exc}"

    # ----- description ----------------------------------------------------

    def _config_value(self, name: str) -> str:
        gp = self.gp
        try:
            if self._config is None:
                self._config = self.cam.get_config()
            status, widget = gp.gp_widget_get_child_by_name(self._config, name)
            if status >= gp.GP_OK:
                value = widget.get_value()
                return "" if value is None else str(value).strip()
        except Exception:
            pass
        return ""

    def battery_now(self):
        """Asked between photos during a backup, so it reads the camera's settings afresh."""
        if self.simulate_dir:
            return None
        from .. import power
        self._config = None
        return power.percent(self._config_value("batterylevel"))

    def info(self) -> SourceInfo:
        if self.simulate_dir:
            return SourceInfo(
                kind="simulated camera",
                model="Simulated camera",
                serial=f"sim:{self.simulate_dir}",
            )
        model = self._config_value("cameramodel")
        try:
            listed = str(self.cam.get_abilities().model)
        except Exception:
            listed = ""
        if not model:
            model = listed or "Camera"
        name = camera_name(self._config_value("manufacturer"), model, listed)
        serial = self._config_value("serialnumber").lstrip("0")
        summary = ""
        if not serial:
            try:
                summary = str(self.cam.get_summary())
                match = re.search(r"Serial Number:\s*(\S+)", summary)
                if match:
                    serial = match.group(1).lstrip("0")
            except Exception:
                pass
        battery = self._config_value("batterylevel")
        storage = []
        try:
            for st in self.cam.get_storageinfo():
                label = (getattr(st, "label", "") or getattr(st, "description", "") or st.basedir).strip()
                total = getattr(st, "capacitykbytes", 0) * 1024
                free = getattr(st, "freekbytes", 0) * 1024
                from ..util import human_bytes

                storage.append(f"{label} ({st.basedir}): {human_bytes(free)} free of {human_bytes(total)}")
        except Exception:
            pass
        return SourceInfo(
            kind="camera",
            model=name,
            serial=serial or f"unknown:{model}",
            battery=battery,
            storage=storage,
        )

    def list_files(self, progress=None) -> list[SourceFile]:
        gp = self.gp
        found: list[SourceFile] = []

        def walk(folder: str, depth: int = 0) -> None:
            if depth > 8:
                return
            files = self.cam.folder_list_files(folder)
            for i in range(files.count()):
                name = files.get_name(i)
                if name.startswith("."):
                    continue
                info = self.cam.file_get_info(folder, name)
                found.append(SourceFile(folder, name, int(info.file.size), int(info.file.mtime)))
                if progress and len(found) % 10 == 0:
                    progress(len(found))
            subs = self.cam.folder_list_folders(folder)
            for i in range(subs.count()):
                sub = subs.get_name(i)
                walk(folder.rstrip("/") + "/" + sub, depth + 1)

        try:
            walk("/")
        except gp.GPhoto2Error as exc:
            raise SourceError(self._explain(exc)) from exc

        # A camera card keeps photos under DCIM. If we can see one, ignore the rest.
        in_dcim = [f for f in found if "/DCIM/" in f.folder + "/" or f.folder.endswith("/DCIM")]
        return in_dcim or found

    def read_chunk(self, f: SourceFile, offset: int, size: int) -> bytes:
        gp = self.gp
        if not self._chunks:
            raise NotSupported("This camera cannot read files in pieces.")
        buf = bytearray(size)
        try:
            got = self.cam.file_read(f.folder, f.name, gp.GP_FILE_TYPE_NORMAL, offset, buf)
        except gp.GPhoto2Error as exc:
            if getattr(exc, "code", None) == gp.GP_ERROR_NOT_SUPPORTED:
                self._chunks = False
                raise NotSupported("This camera cannot read files in pieces.") from exc
            raise SourceError(self._explain(exc)) from exc
        return memoryview(buf)[:got]

    def read_whole(self, f: SourceFile) -> bytes:
        gp = self.gp
        try:
            camera_file = self.cam.file_get(f.folder, f.name, gp.GP_FILE_TYPE_NORMAL)
            data = camera_file.get_data_and_size()
        except gp.GPhoto2Error as exc:
            raise SourceError(self._explain(exc)) from exc
        self._keep = camera_file
        return memoryview(data)
