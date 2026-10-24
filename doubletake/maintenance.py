"""Maintenance mode: one visit to the plain desktop, from DoubleTake only.

Settings, Device, Maintenance logs out to the desktop for one login, for upgrades and
settings. DoubleTake does not open on it. The wallpaper says it is maintenance mode,
a notification says the same, and the dock gets a Restart to DoubleTake button.
DoubleTake only stays on: the next login, after a restart or a log out, is Field
Station again, and the desktop is put back as it was.

How it works:
  1. Before logging out, a flag file is written and the login is switched to the desktop.
  2. At the desktop login, an autostart entry runs `doubletake maintenance`, which
     switches the next login back to DoubleTake and dresses the desktop.
  3. When DoubleTake starts in the kiosk session again, the desktop is put back.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from . import __version__, config, nas, poweron
from .util import version_text

ASSETS = Path(__file__).resolve().parent / "assets"
AUTOSTART = Path(os.path.expanduser("~/.config/autostart/doubletake-maintenance.desktop"))
RESTART_APP = "doubletake-restart.desktop"
OLD_RESTART_APP = "fieldstation-restart.desktop"  # its name before 0.8.1
RESTART_ENTRY = Path(os.path.expanduser("~/.local/share/applications")) / RESTART_APP
WALLPAPER = Path(os.path.expanduser("~/.local/share/doubletake/maintenance-wallpaper.png"))
BACKGROUND = "org.gnome.desktop.background"
# (key, value while in maintenance); a key missing on this desktop is left alone.
DESKTOP_KEYS = (("picture-uri", None), ("picture-uri-dark", None), ("picture-options", "'zoom'"),
                ("primary-color", "'#0A0E12'"))


def flag_path() -> Path:
    return config.config_path().parent / "maintenance.json"


def _load() -> dict:
    try:
        return json.loads(flag_path().read_text())
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = flag_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def active() -> bool:
    """Is a maintenance visit asked for, or under way?"""
    return flag_path().is_file()


def _command() -> str:
    return shutil.which("doubletake") or os.path.expanduser("~/.local/bin/doubletake")


def write_autostart() -> None:
    AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART.write_text(
        "[Desktop Entry]\nType=Application\nName=DoubleTake maintenance mode\n"
        f"Exec={_command()} maintenance\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n",
        encoding="utf-8")


# ----- 1. before logging out -----------------------------------------------------------


def start() -> str:
    """Ask for one maintenance visit to the desktop. Returns an error, or ''."""
    return ask() or switch_login()


def ask() -> str:
    """The visit's flag and the desktop's autostart entry. Returns an error, or ''."""
    try:
        _save({"state": "asked", "asked": time.time()})
        write_autostart()
    except OSError as exc:
        return f"Could not start maintenance mode: {exc}"
    return ""


def switch_login() -> str:
    """The next login: the desktop. Returns an error, or ''."""
    answer = nas.helper("session", "desktop")
    if not answer.get("ok"):
        finish()
        return answer.get("error", "The login could not be switched to the desktop.")
    return ""


# ----- 2. at the desktop login ---------------------------------------------------------


def _gsettings(*args) -> tuple:
    return poweron._gsettings(*args)


QUIET_EDITIONS = {"tron"}  # with this edition on, the desktop keeps its own wallpaper, with only the corner
OS_RELEASE = Path("/etc/os-release")
ICON_DIRS = ("/usr/share/pixmaps", "/usr/share/icons/hicolor/256x256/apps", "/usr/share/icons/hicolor/128x128/apps",
             "/usr/share/icons/hicolor/96x96/apps", "/usr/share/icons/hicolor/64x64/apps",
             "/usr/share/icons/hicolor/48x48/apps")
SVG_DIR = "/usr/share/icons/hicolor/scalable/apps"


def host_name() -> str:
    return socket.gethostname().split(".")[0].lower()


def quiet() -> bool:
    """No maintenance wallpaper, notification or dock button: the desktop's own wallpaper,
    with only the corner (version, name, system) drawn on it. With an edition switched on."""
    return (nas.system_state().get("edition") or "") in QUIET_EDITIONS


def os_release(path: Path = OS_RELEASE) -> dict:
    out = {}
    try:
        for row in path.read_text().splitlines():
            key, _, value = row.partition("=")
            if key and value:
                out[key.strip()] = value.strip().strip('"')
    except OSError:
        pass
    return out


def system_logo(release: dict, roots=("",), convert=None):
    """The system's own logo, as the desktop's About screen shows it (os-release LOGO=), as
    a PNG path, or None. Never shipped with DoubleTake: read from the system it runs on."""
    names = [n for n in (release.get("LOGO"), "fedora-logo-icon", "system-logo") if n]
    for root in roots:
        for name in names:
            for folder in ICON_DIRS:
                png = Path(root + folder) / f"{name}.png"
                if png.is_file():
                    return png
        for name in names:
            svg = Path(root + SVG_DIR) / f"{name}.svg"
            if svg.is_file():
                png = (convert or _svg_to_png)(svg)
                if png:
                    return png
    return None


def _svg_to_png(svg: Path):
    target = WALLPAPER.with_name("system-logo.png")
    for args in (["rsvg-convert", "-w", "256", "-h", "256", "-o", str(target), str(svg)],
                 ["gdk-pixbuf-thumbnailer", "-s", "256", str(svg), str(target)]):
        if shutil.which(args[0]):
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if subprocess.run(args, capture_output=True, timeout=30).returncode == 0 and target.is_file():
                    return target
            except (OSError, subprocess.SubprocessError):
                pass
    return None


def make_wallpaper(path: Path = WALLPAPER, size=(1920, 1080), host: str = "", release=None, logo=None) -> Path:
    """Draw the maintenance wallpaper, in the Cockpit look."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = size
    img = Image.new("RGB", size, "#0A0E12")
    d = ImageDraw.Draw(img)

    def font(name, px):
        try:
            return ImageFont.truetype(str(ASSETS / name), px)
        except OSError:
            return ImageFont.load_default()

    s = h / 1080
    label = font("plex-sans-condensed-600.ttf", int(26 * s))
    head = font("plex-sans-condensed-600.ttf", int(58 * s))
    body = font("plex-sans-condensed-400.ttf", int(32 * s))
    mono = font("plex-mono-500.ttf", int(24 * s))
    lines = [
        (label, "DOUBLETAKE  ·  MAINTENANCE MODE", "#FFB547", int(34 * s)),
        (head, "Restart the tablet to return to DoubleTake.", "#E6EDF3", int(88 * s)),
        (body, "The plain desktop, for one visit: upgrades and settings.", "#8A97A6", int(48 * s)),
        (body, "DoubleTake only stays on.", "#8A97A6", int(48 * s)),
    ]
    pad = int(56 * s)
    width = max(d.textlength(text, font=f) for f, text, _, _ in lines) + int(34 * s)
    height = sum(step for _, _, _, step in lines) + pad
    pw, ph = int(width + 2 * pad), int(height + pad)
    x0, y0 = (w - pw) // 2, (h - ph) // 2
    # A panel with a thin edge, as on the screens.
    d.rounded_rectangle([x0, y0, x0 + pw, y0 + ph], radius=int(22 * s), fill="#121820", outline="#1E2833",
                        width=max(1, int(2 * s)))
    x, y = x0 + pad, y0 + pad
    dot = int(18 * s)
    d.ellipse([x, y + int(5 * s), x + dot, y + int(5 * s) + dot], fill="#FFB547")
    for i, (f, text, colour, step) in enumerate(lines):
        d.text((x + (int(34 * s) if i == 0 else 0), y), text, font=f, fill=colour)
        y += step
    draw_footer(img, host, release, logo, mono)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path


def _plex_mono(px: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(str(ASSETS / "plex-mono-500.ttf"), px)
    except OSError:
        return ImageFont.load_default()


def draw_footer(img, host: str = "", release=None, logo=None, mono=None, left: int = 0) -> None:
    """The corner: the system's logo, then DoubleTake's version and the tablet's name, and
    the system's own name and version under them, in a quiet grey."""
    from PIL import Image, ImageDraw

    w, h = img.size
    s = h / 1080
    mono = mono or _plex_mono(int(24 * s))
    d = ImageDraw.Draw(img)
    release = os_release() if release is None else release
    logo = system_logo(release) if logo is None else logo
    corner = f"DoubleTake {version_text(__version__)}" + (f"  ·  {host}" if host else "")
    system = release.get("PRETTY_NAME", "")
    x = left + int(40 * s)
    line = int(34 * s)
    top = h - int(56 * s) - (line if system else 0)
    if logo:
        try:
            mark = Image.open(logo).convert("RGBA")
            side = line * 2 if system else int(line * 1.4)
            mark.thumbnail((side, side), Image.LANCZOS)
            img.paste(mark, (x, top + (side - mark.height) // 2 - int(4 * s)), mark)
            x += side + int(22 * s)
        except (OSError, ValueError):
            pass
    d.text((x, top), corner, font=mono, fill="#56636F")
    if system:
        d.text((x, top + line), system, font=mono, fill="#56636F")


# ----- the quiet desktop: the wallpaper in use, with only the corner on it ---------------------

FOOTER = WALLPAPER.with_name("desktop-footer")  # + -light.jpg / -dark.jpg
DRM = Path("/sys/class/drm")


def screen_size(root: Path = DRM):
    """The built-in screen's size in pixels, (1920, 1080), from the panel's own list of
    modes (its first is its native one); another connected screen if there is no built-in
    one; None when nothing says."""
    found = []
    try:
        outputs = sorted(root.iterdir())
    except OSError:
        return None
    for out in outputs:
        try:
            if (out / "status").read_text().strip() != "connected":
                continue
            first = (out / "modes").read_text().split()[0]
            w, _, h = first.partition("x")
            size = (int(w), int(h.rstrip("i")))
        except (OSError, ValueError, IndexError):
            continue
        builtin = any(k in out.name for k in ("eDP", "LVDS", "DSI"))
        found.append((not builtin, size))
    return sorted(found)[0][1] if found else None


def _uri_file(uri: str):
    """A wallpaper setting ('file:///…' in quotes) as a picture file, or None. A GNOME
    slideshow (.xml) gives its first picture."""
    from urllib.parse import unquote, urlparse

    uri = uri.strip().strip("'\"")
    if not uri.startswith("file://"):
        return None
    path = Path(unquote(urlparse(uri).path))
    if path.suffix.lower() == ".xml":
        import re

        try:
            found = re.search(r"<file>\s*([^<]+?)\s*</file>", path.read_text(errors="replace"))
        except OSError:
            return None
        path = Path(found.group(1)) if found else None
    return path if path is not None and path.is_file() else None


def footer_on(source: Path, target: Path, host: str = "", release=None, logo=None, screen=None) -> Path:
    """A copy of the picture with the corner drawn on it. Given the screen's size, the copy
    is that size, cut to fill it as the desktop would ('zoom'), so the corner sits where it
    does on the maintenance wallpaper."""
    from PIL import Image

    img = Image.open(source)
    img = img.convert("RGB")
    if screen:
        sw, sh = screen
        w, h = img.size
        scale = max(sw / w, sh / h)
        cw, ch = round(sw / scale), round(sh / scale)
        x, y = (w - cw) // 2, (h - ch) // 2
        img = img.crop((x, y, x + cw, y + ch)).resize((sw, sh), Image.LANCZOS)
    draw_footer(img, host, release, logo)
    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target, "JPEG", quality=94)
    return target


def footer_desktop(data: dict) -> dict:
    """On the quiet tablet: the wallpaper it already has, with the corner on it. Nothing else."""
    saved = data.setdefault("saved", {})
    host = socket.gethostname().split(".")[0]
    screen = screen_size()
    drawn = False
    for key, tail in (("picture-uri", "-light.jpg"), ("picture-uri-dark", "-dark.jpg")):
        if _gsettings("range", BACKGROUND, key)[0] != 0:
            continue
        code, current = _gsettings("get", BACKGROUND, key)
        if code != 0:
            continue
        if str(WALLPAPER.parent) in current:
            current = saved.get(key, "")  # left from a visit that did not finish: the picture under it
        source = _uri_file(current)
        if source is None:
            continue
        try:
            done = footer_on(source, FOOTER.with_name(FOOTER.name + tail), host, screen=screen)
        except Exception:
            continue  # a picture this cannot read: left as it is
        saved.setdefault(key, current)
        _gsettings("set", BACKGROUND, key, f"'{done.as_uri()}'")
        drawn = True
    if drawn and screen and _gsettings("range", BACKGROUND, "picture-options")[0] == 0:
        # Drawn at the screen's size: shown as it is, whatever the desktop was set to.
        if "picture-options" not in saved:
            code, current = _gsettings("get", BACKGROUND, "picture-options")
            if code == 0:
                saved["picture-options"] = current
        _gsettings("set", BACKGROUND, "picture-options", "'zoom'")
    return data


def _notify(title: str, text: str) -> None:
    for args in (["notify-send", "--app-name=DoubleTake", "-i", "system-reboot", title, text],
                 ["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                  "--object-path", "/org/freedesktop/Notifications", "--method",
                  "org.freedesktop.Notifications.Notify", "DoubleTake", "0", "system-reboot", title, text,
                  "[]", "{}", "0"]):
        if shutil.which(args[0]):
            try:
                if subprocess.run(args, capture_output=True, timeout=10).returncode == 0:
                    return
            except (OSError, subprocess.SubprocessError):
                pass


def _favorites() -> list:
    code, out = _gsettings("get", "org.gnome.shell", "favorite-apps")
    if code != 0:
        return []
    try:
        import ast

        text = out[out.index("["):] if "[" in out else "[]"
        return [str(x) for x in ast.literal_eval(text)]
    except (ValueError, SyntaxError):
        return []


def _set_favorites(apps: list) -> None:
    _gsettings("set", "org.gnome.shell", "favorite-apps", "[" + ", ".join(repr(a) for a in apps) + "]")


def dress_desktop(data: dict) -> dict:
    """Wallpaper, dock button and notification. The desktop's own values are kept in 'data'."""
    saved = data.setdefault("saved", {})
    try:
        uri = make_wallpaper(host=socket.gethostname().split(".")[0]).as_uri()
    except Exception:
        uri = ""
    for key, value in DESKTOP_KEYS:
        if _gsettings("range", BACKGROUND, key)[0] != 0:
            continue  # not on this desktop
        value = value if value is not None else (f"'{uri}'" if uri else None)
        if value is None:
            continue
        if key not in saved:
            code, current = _gsettings("get", BACKGROUND, key)
            if code == 0:
                saved[key] = current
        _gsettings("set", BACKGROUND, key, value)
    try:
        RESTART_ENTRY.parent.mkdir(parents=True, exist_ok=True)
        RESTART_ENTRY.write_text(
            "[Desktop Entry]\nType=Application\nName=Restart to DoubleTake\n"
            "Comment=Restart the tablet, which returns to DoubleTake\n"
            "Exec=systemctl reboot\nIcon=system-reboot\nTerminal=false\nCategories=System;\n", encoding="utf-8")
        favorites = _favorites()
        if RESTART_APP not in favorites:
            data["dock_added"] = True
            _set_favorites(favorites + [RESTART_APP])
    except OSError:
        pass
    return data


def at_desktop_login() -> int:
    """Run by the desktop's autostart: the maintenance visit begins."""
    from . import kiosk

    if kiosk.in_kiosk():
        return 0  # only the desktop is dressed
    data = _load()
    from_boot = bool(nas.system_state().get("boot_maintenance"))
    if not data and from_boot:
        # Chosen in the boot menu (hold Shift at power-on): a visit, as if asked from the screens.
        data = {"state": "asked", "asked": time.time(), "from": "boot"}
    if not data:
        return 0  # an ordinary login; the entry stays, ready for a visit from the boot menu
    if data.get("state") == "asked":
        # DoubleTake only stays on: the next login is DoubleTake again.
        if data.get("from") == "boot":
            answer = nas.helper("boot-maintenance", "done")  # automatic login, and DoubleTake only, as before
        else:
            answer = nas.helper("session", "kiosk")
        data["back_to_kiosk"] = bool(answer.get("ok"))
        data["state"] = "visiting"
        data = footer_desktop(data) if quiet() else dress_desktop(data)
        _save(data)
        _offer_updates()
        if data["back_to_kiosk"]:
            if not quiet():
                _notify("DoubleTake maintenance mode",
                        "The full desktop, for one visit. Restart the tablet to return to DoubleTake.")
        else:
            _notify("DoubleTake maintenance mode",
                    "Could not switch the next login back to DoubleTake: "
                    + answer.get("error", "unknown") + ". Open DoubleTake, Settings, Device.")
    return 0


def _offer_updates() -> None:
    """Look, in the background, whether updates are due; a terminal opens if they are."""
    try:
        subprocess.Popen([_command(), "update", "--auto"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# ----- 3. back in DoubleTake only -------------------------------------------------------


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def finish() -> None:
    """Put the desktop back as it was, and end the visit."""
    data = _load()
    for key, value in (data.get("saved") or {}).items():
        if key.startswith("picture-uri"):
            code, current = _gsettings("get", BACKGROUND, key)
            if code == 0 and str(WALLPAPER.parent) not in current:
                continue  # a wallpaper chosen during the visit stays
        _gsettings("set", BACKGROUND, key, value)
    if data.get("dock_added"):
        _set_favorites([a for a in _favorites() if a not in (RESTART_APP, OLD_RESTART_APP)])
    for path in (RESTART_ENTRY, AUTOSTART, flag_path(), WALLPAPER,
                 FOOTER.with_name(FOOTER.name + "-light.jpg"), FOOTER.with_name(FOOTER.name + "-dark.jpg")):
        _remove(path)
