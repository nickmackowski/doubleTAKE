"""The settings the screens can change, with what each one may be set to."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from . import __version__, config, naming
from .util import safe_name, version_text

THEMES = ["daylight", "slate", "night", "nightred"]
LOOKS = ["classic", "cockpit"]
COCKPIT_THEMES = ["cockpit", "cockpit-light", "cockpit-red"]
NIGHT_RED = {"classic": "nightred", "cockpit": "cockpit-red"}  # the red theme of each look (the P2 key)


def screen_theme(cfg: dict) -> tuple[str, str]:
    """The look and the theme the screens are drawn in: (look, theme)."""
    if cfg.get("look") == "cockpit":
        theme = cfg.get("cockpit_theme")
        return "cockpit", theme if theme in COCKPIT_THEMES else "cockpit"
    theme = cfg.get("theme")
    return "classic", theme if theme in THEMES else "daylight"
KEEP_DAYS = [7, 14, 30, 60, 90]
MAX_LINKS = 4


def phone_logo(cfg: dict) -> Path:
    """The photographer's logo for the phone page. Kept on this tablet only."""
    return config.data_dir(cfg) / "phone-logo.png"


def web_address(text: str) -> str:
    """A link as it can be opened: 'nickm.com' -> 'https://nickm.com'. '' when it is not one."""
    text = (text or "").strip()
    if not text or len(text) > 200 or any(c.isspace() or not c.isprintable() or c in '<>"\\' for c in text):
        return ""
    if "://" not in text:
        text = "https://" + text
    scheme, rest = text.split("://", 1)
    host = rest.split("/", 1)[0]
    if scheme.lower() not in ("http", "https") or "." not in host or host.startswith(".") or "@" in host:
        return ""
    return scheme.lower() + "://" + rest


def save_logo(cfg: dict, data: bytes) -> str:
    """Keep a logo for the phone page: any picture, made into a PNG no wider than 1200.
    Returns why not, or ''."""
    if not data:
        target = phone_logo(cfg)
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            return f"Could not remove it: {exc}"
        return ""
    try:
        import io

        from PIL import Image, ImageOps

        Image.MAX_IMAGE_PIXELS = 120_000_000
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGBA")
        box = img.getbbox()  # trim empty edges
        if box:
            img = img.crop(box)
        img.thumbnail((1200, 400))
    except Exception:
        return "That is not a picture it can read. Use a PNG or a JPEG."
    target = phone_logo(cfg)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(".phone-logo.part")
        img.save(tmp, "PNG", optimize=True)
        os.replace(tmp, target)
    except OSError as exc:
        return f"Could not keep it: {exc}"
    return ""
AUTOSTART = Path(os.path.expanduser("~/.config/autostart/doubletake.desktop"))


def public(cfg: dict) -> dict:
    """What the settings screen shows."""
    second = cfg.get("second_copy", "auto")
    return {
        "theme": cfg.get("theme", "daylight"),
        "look": screen_theme(cfg)[0],
        "cockpit_theme": cfg.get("cockpit_theme") if cfg.get("cockpit_theme") in COCKPIT_THEMES else "cockpit",
        "screen_dark_after": int(cfg.get("screen_dark_after", 5)),
        "sleep_on_battery": int(cfg.get("sleep_on_battery", 30)),
        "auto_backup": bool(cfg.get("auto_backup", False)),
        "auto_dark": bool(cfg.get("auto_dark", True)),
        "auto_chime": bool(cfg.get("auto_chime", True)),
        "update_days": cfg.get("update_days") if cfg.get("update_days") in (0, 30, 60, 90) else 30,
        "copy_what": cfg.get("copy_what", "raw_jpeg") if cfg.get("copy_what") in ("raw", "raw_jpeg", "all") else "raw_jpeg",
        "quick_names": list(cfg.get("quick_names", [])),
        "library": cfg["library"],
        "data_dir": str(config.data_dir(cfg)),
        "second_copy": "off" if second == "off" else "on",
        "second_copy_path": second if second not in ("auto", "off") else "",
        "second_copy_folder": cfg.get("second_copy_folder", "FieldPhotos"),
        "after_import": cfg.get("after_import", "keep"),
        "keep_days": int(cfg.get("keep_days", 30)),
        "start_at_login": AUTOSTART.is_file(),
        "hotspot_name": cfg.get("hotspot_name") or "DoubleTake",
        "hotspot_password": cfg.get("hotspot_password", ""),
        "hotspot_address": "10.42.0.1",
        "nas_cable": bool(cfg.get("nas_cable", True)),
        "nas_hotspot": bool(cfg.get("nas_hotspot", True)),
        "nas_trusted": list(cfg.get("nas_trusted") or []),
        "photo_sort": cfg.get("photo_sort", "taken_asc"),
        "rename_on_import": bool(cfg.get("rename_on_import", False)),
        "direct_cable": bool(cfg.get("direct_cable", True)),
        "second_copy_device": cfg.get("second_copy_device", "microsd"),
        "rename_pattern": cfg.get("rename_pattern") or naming.DEFAULT_PATTERN,
        "rename_tokens": naming.TOKENS,
        "viewer_zoom": int(cfg.get("viewer_zoom", 100)),
        "viewer_info": bool(cfg.get("viewer_info", True)),
        "owner_name": cfg.get("owner_name") or "",
        "owner_website": cfg.get("owner_website") or "",
        "phone_links": [dict(link) for link in cfg.get("phone_links") or []][:MAX_LINKS],
        "phone_logo": phone_logo(cfg).is_file(),
        "multi_camera": bool(cfg.get("multi_camera", False)),
        "about": about(cfg),
    }


def apply(cfg: dict, changes: dict) -> str:
    """Check and save changes. Returns an error message, or '' when all were saved."""
    clean: dict = {}
    for key, value in changes.items():
        if key == "theme" and value in THEMES:
            clean[key] = value
        elif key == "screen_dark_after" and value in (0, 2, 5, 10):
            clean[key] = value
        elif key == "sleep_on_battery" and value in (0, 15, 30, 60):
            clean[key] = value
        elif key in ("auto_backup", "auto_dark", "auto_chime") and isinstance(value, bool):
            clean[key] = value
        elif key == "copy_what" and value in ("raw", "raw_jpeg", "all"):
            clean[key] = value
        elif key == "look" and value in LOOKS:
            clean[key] = value
        elif key == "cockpit_theme" and value in COCKPIT_THEMES:
            clean[key] = value
        elif key == "update_days" and value in (0, 30, 60, 90) and not isinstance(value, bool):
            clean[key] = value
        elif key == "night_red_from_classic" and value in THEMES:
            clean[key] = value
        elif key == "night_red_from_cockpit" and value in COCKPIT_THEMES:
            clean[key] = value
        elif key == "after_import" and value in ("keep", "delete"):
            clean[key] = value
        elif key == "keep_days" and value in KEEP_DAYS:
            clean[key] = value
        elif key == "second_copy" and value in ("on", "off"):
            clean[key] = "auto" if value == "on" else "off"
        elif key == "quick_names" and isinstance(value, list):
            names = []
            for name in value:
                name = safe_name(str(name))[:40]
                if name and name != "shoot" and name not in names:
                    names.append(name)
            clean[key] = names[:8]
        elif key == "hotspot_name" and isinstance(value, str) and 1 <= len(value.strip()) <= 32:
            clean[key] = value.strip()
        elif key == "hotspot_password" and isinstance(value, str) and 8 <= len(value) <= 63 and value.isprintable():
            clean[key] = value
        elif key in ("nas_cable", "nas_hotspot") and isinstance(value, bool):
            clean[key] = value
        elif key == "nas_trusted" and isinstance(value, list) and all(isinstance(v, str) for v in value):
            clean[key] = sorted({v for v in value if v and len(v) <= 64})
        elif key == "direct_cable" and isinstance(value, bool):
            clean[key] = value
        elif key == "second_copy_device" and value in ("microsd", "usb"):
            clean[key] = value
        elif key == "rename_on_import" and isinstance(value, bool):
            clean[key] = value
        elif key == "rename_pattern" and isinstance(value, str):
            problem = naming.check(value)
            if problem:
                return problem
            clean[key] = value.strip()
        elif key == "photo_sort" and value in ("taken_asc", "taken_desc", "name_asc", "name_desc"):
            clean[key] = value
        elif key == "viewer_zoom" and value in (50, 75, 100):
            clean[key] = value
        elif key in ("viewer_info", "multi_camera") and isinstance(value, bool):
            clean[key] = value
        elif key in ("owner_name", "owner_website") and isinstance(value, str):
            value = " ".join(value.split())
            if len(value) > 80 or not value.isprintable():
                return "That is too long." if len(value) > 80 else "That has a character it cannot keep."
            if key == "owner_website" and value and not web_address(value):
                return "That does not look like a web address."
            clean[key] = value
        elif key == "phone_links" and isinstance(value, list):
            links = []
            for link in value[:MAX_LINKS]:
                if not isinstance(link, dict):
                    return "Not a link."
                label = " ".join(str(link.get("label") or "").split())[:30]
                url = str(link.get("url") or "").strip()
                if not label and not url:
                    continue
                if not web_address(url):
                    return f"{label or 'That link'}: that does not look like a web address."
                links.append({"label": label or web_address(url).split("//", 1)[1], "url": url})
            clean[key] = links
        elif key == "start_at_login" and isinstance(value, bool):
            error = set_autostart(value)
            if error:
                return error
        else:
            return f"Cannot set {key} to {value!r}."
    if clean:
        config.save(clean)
        cfg.update(clean)
    return ""


def set_autostart(on: bool) -> str:
    """Open the screens when the user logs in to the desktop."""
    if not on:
        try:
            AUTOSTART.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            return f"Could not switch it off: {exc}"
        return ""
    command = shutil.which("doubletake") or os.path.expanduser("~/.local/bin/doubletake")
    try:
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text(
            "[Desktop Entry]\nType=Application\nName=DoubleTake\n"
            f"Exec={command} gui --autostart\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n",
            encoding="utf-8",
        )
    except OSError as exc:
        return f"Could not switch it on: {exc}"
    return ""


def _libraries() -> tuple:
    """The camera library and the image library, with their versions."""
    camera = image = ""
    try:
        import gphoto2 as gp
        camera = f"python-gphoto2 {gp.__version__}, libgphoto2 {gp.gp_library_version(gp.GP_VERSION_SHORT)[0]}"
    except Exception:
        camera = "not installed"
    try:
        import PIL
        image = f"Pillow {PIL.__version__}"
    except Exception:
        image = "not installed"
    return camera, image


def about(cfg: dict | None = None) -> dict:
    def read(path):
        try:
            return Path(path).read_text().strip()
        except OSError:
            return ""

    camera, image = _libraries()
    system = ""
    for row in read("/etc/os-release").splitlines():
        if row.startswith("PRETTY_NAME="):
            system = row.split("=", 1)[1].strip().strip('"')
    return {
        "version": version_text(__version__),
        "tablet": " ".join(x for x in (read("/sys/class/dmi/id/sys_vendor"),
                                        read("/sys/class/dmi/id/product_name")) if x),
        "system": system or platform.platform(),
        "python": platform.python_version(),
        "host": platform.node().split(".")[0],
        "camera_lib": camera,
        "image_lib": image,
        "program": str(Path(__file__).resolve().parent.parent),
        "photos": str(Path(cfg.get("library") or config.default_library()).expanduser()) if cfg is not None else "",
        "records": str(config.data_dir(cfg)) if cfg is not None else "",
    }


def open_app(which: str) -> str:
    """Open the desktop's file manager or terminal on top of the screens."""
    choices = {
        "files": [["nautilus", os.path.expanduser("~")], ["xdg-open", os.path.expanduser("~")]],
        "terminal": [["gnome-terminal", "--geometry=80x24", "--zoom=1.35"], ["ptyxis"], ["konsole"], ["xterm"]],
    }
    for command in choices.get(which, []):
        if shutil.which(command[0]):
            try:
                subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                return ""
            except OSError as exc:
                return str(exc)
    return "Not found on this tablet."
