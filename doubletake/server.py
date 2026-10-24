"""Serves the touch screens to a browser on this tablet.

The screens are plain web pages. They ask this server for the engine's state
a couple of times a second, and send it the buttons that are pressed. The
server listens on this tablet only (127.0.0.1), never on the network.
"""

from __future__ import annotations

import errno
import json
import mimetypes
import os
import shutil
import subprocess
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, bluetooth, editor, naming, photos, settings
from . import kiosk as kiosk_session
from .station import Station

WEB = Path(__file__).resolve().parent / "web"
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/javascript", ".js")


def make_handler(station: Station, on_quit):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"DoubleTake/{__version__}"

        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        # ----- replies --------------------------------------------------------

        def _json(self, data, status=200):
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def handle(self):
            # The screens closing or reloading mid-answer: nothing went wrong, so nothing to log.
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _file(self, path: Path, cache: str = "no-store"):
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def _jpeg(self, body: bytes):
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _photo(self, parts, query):
            """/photo/<id>/thumb.jpg, /photo/<id>/screen.jpg, /photo/<id>/crop.jpg?x=&y=&w=&h="""
            try:
                file_id = int(parts[1])
            except (IndexError, ValueError):
                return self._json({"error": "not found"}, 404)
            found = station.photo_file(file_id)
            kind = parts[2] if len(parts) > 2 else ""
            if found is None:
                return self._json({"error": "not found"}, 404)
            path, cache = found
            if kind in ("thumb.jpg", "screen.jpg"):
                made = photos.picture(path, kind[:-4], cache)
                if made is None:
                    return self._json({"error": "no preview"}, 404)
                return self._file(made, "max-age=86400")
            if kind == "edit.jpg":
                try:
                    params = json.loads((query.get("p") or ["{}"])[0])
                except ValueError:
                    params = {}
                body = station.edit_preview(file_id, params, crop=(query.get("crop") or ["1"])[0] != "0")
                if body is None:
                    return self._json({"error": "no preview"}, 404)
                return self._jpeg(body)
            if kind == "crop.jpg":
                def number(name, default):
                    try:
                        return float((query.get(name) or [default])[0])
                    except ValueError:
                        return default
                scale = number("s", 1.0)
                scale = scale if scale in (0.5, 0.75) else 1.0
                body = photos.crop(path, min(1.0, max(0.0, number("x", 0.5))), min(1.0, max(0.0, number("y", 0.5))),
                                   int(min(4000 / scale, number("w", 1600))), int(min(3000 / scale, number("h", 1000))),
                                   scale)
                if body is None:
                    return self._json({"error": "no preview"}, 404)
                return self._jpeg(body)
            return self._json({"error": "not found"}, 404)

        def _export(self, path, query):
            """/export/<shoot>/<name>?t=thumb|full: an exported JPEG, or a small picture of it."""
            parts = [unquote(p) for p in path.split("/")[2:]]
            found = editor.export_file(station.library, *parts) if len(parts) == 2 else None
            if found is None:
                return self._json({"error": "not found"}, 404)
            if (query.get("t") or ["full"])[0] == "thumb":
                body = photos.small(found, 480)
                if body is not None:
                    return self._jpeg(body)
            return self._file(found)

        def _local(self) -> bool:
            """Only answer a browser that is talking to this tablet by its local name."""
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        # ----- reading --------------------------------------------------------

        def do_GET(self):
            if not self._local():
                return self._json({"error": "local only"}, 403)
            url = urlparse(self.path)
            if url.path == "/api/state":
                return self._json(station.state())
            query = parse_qs(url.query)
            if url.path == "/api/flicker":
                return self._json(station.flicker_status())
            if url.path == "/api/status":
                return self._json(station.status())
            if url.path == "/api/folder":
                name = (query.get("name") or [""])[0]
                return self._json({"folder": station.folder_preview(name)})
            if url.path == "/api/library":
                return self._json(station.library_view())
            if url.path in ("/api/shoot", "/api/photo"):
                try:
                    item = int((query.get("id") or ["0"])[0])
                except ValueError:
                    item = 0
                found = station.shoot_photos(item) if url.path == "/api/shoot" else station.photo_info(item)
                return self._json(found) if found is not None else self._json({"error": "not found"}, 404)
            if url.path == "/api/settings":
                return self._json(station.get_settings())
            if url.path == "/api/edit":
                try:
                    found = station.edit_info(int((query.get("id") or ["0"])[0]))
                except ValueError:
                    found = None
                return self._json(found) if found is not None else self._json({"error": "not found"}, 404)
            if url.path == "/api/naming":
                pattern = (query.get("pattern") or [""])[0]
                problem = naming.check(pattern)
                return self._json({"error": problem, "example": "" if problem else naming.example(pattern)})
            if url.path == "/api/rename":
                try:
                    item = int((query.get("id") or ["0"])[0])
                except ValueError:
                    item = 0
                return self._json(station.rename_preview(item, (query.get("pattern") or [""])[0]))
            if url.path == "/api/exports":
                return self._json(station.exports())
            if url.path.startswith("/export/"):
                return self._export(url.path, query)
            if url.path.startswith("/photo/"):
                return self._photo(url.path.strip("/").split("/"), query)
            name = "index.html" if url.path in ("", "/") else url.path.lstrip("/")
            target = (WEB / name).resolve()
            if WEB not in target.parents or not target.is_file():
                return self._json({"error": "not found"}, 404)
            return self._file(target, "max-age=86400" if target.suffix == ".woff2" else "no-store")

        # ----- buttons --------------------------------------------------------

        def do_POST(self):
            # The extra header means another web page open in the same browser
            # cannot press these buttons.
            if not self._local() or self.headers.get("X-DoubleTake") != "1":
                return self._json({"error": "local only"}, 403)
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 65536)
                data = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(data, dict):
                    data = {}
            except ValueError:
                data = {}
            path = urlparse(self.path).path
            error = ""
            # The DoubleTake password guards the ways out of DoubleTake and the jobs that
            # cannot be undone. Everything else, every day, works without it.
            changes = data.get("changes") if isinstance(data.get("changes"), dict) else {}
            guarded = (path in ("/api/quit", "/api/card/format", "/api/card/prepare", "/api/reset/settings", "/api/reset/everything",
                                "/api/shoot/clear", "/api/shoot/delete", "/api/flicker")
                       or (path == "/api/leave" and data.get("action") == "desktop")
                       or (path == "/api/settings" and changes.get("kiosk") is False))
            if guarded:
                problem = station.check_password(str(data.get("password") or ""))
                if problem:
                    return self._json({"ok": False, "error": problem, "need_password": True})
            if path == "/api/fs-password/set":
                error = station.set_password(str(data.get("current") or ""), str(data.get("new") or ""))
                return self._json({"ok": not error, "error": error})
            if path == "/api/fs-password/none":
                error = station.set_password(str(data.get("current") or ""), "", none=True)
                return self._json({"ok": not error, "error": error})
            if path == "/api/auto/cancel":
                station.cancel_auto()
                return self._json({"ok": True})
            if path == "/api/flicker":
                done = station.set_flicker(bool(data.get("on")))
                return self._json(dict(done, ok=not done.get("error")))
            if path in ("/api/status/open", "/api/status/close"):
                station.show_status(path.endswith("open"))
                return self._json({"ok": True})
            if path == "/api/media":
                from . import media

                station.wake_screen()
                return self._json(media.press(str(data.get("key") or "")))
            if path == "/api/screen/on":
                station.wake_screen()
                return self._json({"ok": True})
            if path == "/api/camera/scan":
                station.scan()
            elif path == "/api/rename":
                try:
                    done = station.rename_shoot(int(data.get("id") or 0), str(data.get("pattern") or ""))
                except (TypeError, ValueError):
                    done = {"error": "Not a shoot."}
                return self._json(dict(done, ok=not done.get("error")))
            elif path == "/api/edit":
                try:
                    error = station.edit_save(int(data.get("id") or 0), data.get("params"))
                except (TypeError, ValueError):
                    error = "Not a photo."
            elif path == "/api/export":
                ids = [int(x) for x in data.get("ids") or [] if str(x).isdigit()]
                error = station.start_export(ids, str(data.get("size") or ""), str(data.get("quality") or ""))
            elif path == "/api/exports/delete":
                error = station.delete_export(str(data.get("shoot") or ""), str(data.get("name") or ""))
            elif path == "/api/phone/start":
                done = station.phone_start(str(data.get("shoot") or ""))
                return self._json(dict(done, ok=not done.get("error")))
            elif path == "/api/phone/stop":
                station.phone_stop()
            elif path == "/api/usb":
                return self._json({"drives": station.usb_drives()})
            elif path == "/api/usb/copy":
                error = station.usb_copy(str(data.get("mount") or ""), str(data.get("shoot") or ""))
            elif path == "/api/usb/eject":
                error = station.usb_eject(str(data.get("mount") or ""))
            elif path == "/api/backup/start":
                error = station.start_backup(str(data.get("name") or ""))
            elif path == "/api/backup/stop":
                station.stop_backup_now()
            elif path == "/api/backup/dismiss":
                station.dismiss()
            elif path == "/api/mirror/pause":
                station.pause_mirror()
            elif path == "/api/mirror/resume":
                station.resume_mirror()
            elif path == "/api/card/use":
                error = station.card_use()
            elif path == "/api/card/ignore":
                error = station.card_ignore()
            elif path == "/api/shoot/imported":
                done = station.mark_imported(int(data.get("id") or 0), bool(data.get("on")))
                return self._json(dict(done, ok=not done.get("error")))
            elif path in ("/api/shoot/clear", "/api/shoot/delete"):
                ids = [int(x) for x in data.get("ids") or [] if str(x).isdigit()]
                if not ids:
                    return self._json({"ok": False, "error": "No shoots chosen."})
                done = station.clear(ids)
                return self._json(dict(done, ok=not done.get("error")))
            elif path == "/api/settings":
                changes = data.get("changes")
                error = station.set_settings(changes) if isinstance(changes, dict) else "Nothing to change."
            elif path == "/api/open":
                error = settings.open_app(str(data.get("app") or ""))
            elif path == "/api/network/scan":
                return self._json(station.net_scan())
            elif path == "/api/network/connect":
                ssid = str(data.get("ssid") or "")
                if not ssid or not station.net_connect(ssid, str(data.get("password") or "")):
                    error = "Busy with the network. Try again in a moment."
            elif path == "/api/network/disconnect":
                if not station.net_disconnect():
                    error = "Busy with the network. Try again in a moment."
            elif path == "/api/network/hotspot":
                if not station.hotspot(bool(data.get("on"))):
                    error = "Busy with the network. Try again in a moment."
            elif path == "/api/bluetooth":
                return self._json(station.bt_status())
            elif path in ("/api/bluetooth/power", "/api/bluetooth/scan", "/api/bluetooth/pair",
                          "/api/bluetooth/remove", "/api/bluetooth/connect"):
                address = str(data.get("address") or "").upper()
                what = path.rsplit("/", 1)[1]
                if what != "power" and what != "scan" and not bluetooth.MAC_RE.match(address):
                    error = "Not a Bluetooth address."
                else:
                    started = {"power": lambda: station.bt_power(bool(data.get("on"))),
                               "scan": station.bt_scan,
                               "pair": lambda: station.bt_pair(address, str(data.get("name") or "")[:60]),
                               "remove": lambda: station.bt_remove(address),
                               "connect": lambda: station.bt_connect(address, bool(data.get("on", True)))}[what]()
                    if not started:
                        error = "Busy with Bluetooth. Try again in a moment."
            elif path == "/api/nas/switch":
                what = str(data.get("what") or "")
                if what not in ("nas", "sftp") or not station.nas_switch(what, bool(data.get("on"))):
                    error = "Busy. Try again in a moment."
            elif path == "/api/nas/login":
                name, password = str(data.get("name") or ""), str(data.get("password") or "")
                if not name or not password or not station.nas_login(name, password):
                    error = "Give a name and a password."
            elif path == "/api/nas/trust":
                changes = data.get("changes")
                error = station.nas_trust(changes) if isinstance(changes, dict) else "Nothing to change."
            elif path == "/api/nas/networks":
                return self._json({"networks": station.nas_networks()})
            elif path == "/api/card/report":
                return self._json(station.card_report())
            elif path == "/api/card/details":
                return self._json(station.card_details())
            elif path == "/api/card/prepare":
                done = station.card_prepare(str(data.get("agree") or ""))
                return self._json(dict(done, ok=not done.get("error")))
            elif path == "/api/card/format":
                done = station.format_card()
                return self._json(dict(done, ok=not done.get("error")))
            elif path == "/api/reset/settings":
                done = station.reset_settings()
                return self._json(dict(done, ok=not done.get("error")))
            elif path == "/api/reset/everything":
                done = station.erase_everything(bool(data.get("confirmed")))
                return self._json(dict(done, ok=not done.get("error")))
            elif path == "/api/leave":
                # The steps run behind the reply; the switching screen lists them as they finish.
                action = str(data.get("action") or "")
                error = station.leave(action, background=True,
                                      on_gone=on_quit if action in ("desktop", "logout") else None)
                if not error:
                    return self._json({"ok": True, "error": ""})
            elif path == "/api/quit":
                self._json({"ok": True})
                return on_quit()
            else:
                return self._json({"error": "not found"}, 404)
            self._json({"ok": not error, "error": error})

    return Handler


def _already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as reply:
            return "version" in json.load(reply)
    except Exception:
        return False


_KIOSK_PREFS = """\
user_pref("browser.startup.blankWindow", false);
user_pref("browser.display.background_color", "#0A0E12");
user_pref("browser.display.background_color.dark", "#0A0E12");
user_pref("ui.systemUsesDarkTheme", 1);
user_pref("media.autoplay.default", 0);
user_pref("media.autoplay.blocking_policy", 0);
user_pref("layout.css.prefers-color-scheme.content-override", 0);
user_pref("browser.theme.content-theme", 0);
user_pref("browser.theme.toolbar-theme", 0);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("startup.homepage_welcome_url", "");
user_pref("startup.homepage_welcome_url.additional", "");
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.tabs.warnOnClose", false);
user_pref("browser.translations.automaticallyPopup", false);
"""


def open_screens(url: str, kiosk: bool):
    """Open the screens in Firefox. Returns the browser process when it is ours to close."""
    firefox = shutil.which("firefox")
    quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    try:
        if kiosk and firefox:
            # Its own profile, so it is a separate Firefox that can be closed
            # from the Exit button without touching the user's own browser.
            profile = Path(os.path.expanduser("~/.local/share/doubletake/firefox-profile"))
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "user.js").write_text(_KIOSK_PREFS, encoding="utf-8")
            # A dark window from the first moment, before the screens have drawn.
            env = dict(os.environ, GTK_THEME="Adwaita:dark")
            return subprocess.Popen([firefox, "--kiosk", "--no-remote", "--profile", str(profile), url],
                                    env=env, **quiet)
        if firefox:
            subprocess.Popen([firefox, "--new-window", url], **quiet)
        else:
            webbrowser.open(url)
    except OSError:
        pass
    return None


def _keep_screens_open(box: dict, url: str, quitting: threading.Event, wait: float = 2.0) -> None:
    """Open the screens again if Firefox closes or crashes, until DoubleTake stops."""
    while not quitting.wait(wait):
        firefox = box.get("firefox")
        if firefox is not None and firefox.poll() is not None:
            box["firefox"] = open_screens(url, True)


def serve(cfg: dict, source: str = "camera", port: int | None = None, browser: bool = True,
          kiosk: bool = False, limit: int | None = None) -> int:
    port = int(port or cfg.get("port", 8765))
    url = f"http://127.0.0.1:{port}/"
    station = Station(cfg, source, limit=limit)
    quitting = threading.Event()
    box = {}

    def on_quit():
        if not quitting.is_set():
            quitting.set()
            threading.Thread(target=box["server"].shutdown, daemon=True).start()

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(station, on_quit))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE and _already_running(port):
            print(f"DoubleTake is already running. Opening its screens: {url}")
            if browser:
                open_screens(url, kiosk)
            return 0
        print(f"Could not start the screens on port {port}: {exc}")
        return 1
    server.daemon_threads = True
    box["server"] = server
    station.start()

    print(f"DoubleTake {__version__}")
    print(f"Screens:  {url}")
    if limit:
        print(f"Testing:  each backup copies at most {limit} photos")
    print("Stop with the Exit button on the home screen, or Ctrl-C here.")
    if browser and kiosk:
        print("Full screen. For a normal window instead: doubletake gui --window")
    firefox = open_screens(url, kiosk) if browser else None
    if browser and kiosk and firefox is None:
        print("Firefox was not found, so the screens opened in the default browser instead.")
    box["firefox"] = firefox
    if firefox is not None and kiosk_session.in_kiosk():
        # DoubleTake only: there is nothing behind the screens, so they must never stay closed.
        threading.Thread(target=_keep_screens_open, args=(box, url, quitting), name="screens", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    print("Stopping. Anything part-way is picked up again next time.")
    station.shutdown()
    server.server_close()
    firefox = box.get("firefox")
    if firefox is not None:
        try:
            firefox.terminate()
        except OSError:
            pass
    return 0
