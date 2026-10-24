"""The setup questions in a terminal: `doubletake setup`.

The same questions as the first-start pages on the screens, in the same order, asked one at
a time. Every answer is saved as it is given, through the same engine the screens use, so
stopping half-way (Ctrl-C) keeps what was answered and the rest can be done later, here or
in Settings.
"""

from __future__ import annotations

import getpass
import grp
import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.request

from . import __version__, firstrun
from .util import version_text

TTY = sys.stdout.isatty()
STEPS = 9


def _style(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def bold(text):
    return _style("1", text)


def green(text):
    return _style("32", text)


def orange(text):
    return _style("33", text)


def dim(text):
    return _style("2", text)


def say(text: str = "", indent: int = 2) -> None:
    width = max(40, min(shutil.get_terminal_size((80, 20)).columns, 84)) - indent
    for para in text.split("\n"):
        print(textwrap.fill(para, width, initial_indent=" " * indent, subsequent_indent=" " * indent) if para else "")


def heading(n: int, title: str, lead: str = "") -> None:
    print()
    print(dim(f"  Setup {n} of {STEPS}  ") + dim("●" * n + "○" * (STEPS - n)))
    print("  " + bold(title))
    if lead:
        say(lead)
    print()


def good(text):
    say(green("✓ ") + text)


def warn(text):
    say(orange("! ") + text)


def line(prompt: str) -> str:
    try:
        return input("  " + prompt)
    except EOFError:
        raise KeyboardInterrupt


def yes(question: str, default: bool) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        answer = line(f"{question} {hint} ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        say("Type y or n.")


def choose(options: list[tuple[str, str]], current) -> str:
    for i, (value, label) in enumerate(options, 1):
        mark = green("  ← now") if value == current else ""
        print(f"    {i}) {label}{mark}")
    keep = [i for i, (v, _) in enumerate(options, 1) if v == current]
    hint = f" [Enter keeps {keep[0]}]" if keep else ""
    while True:
        answer = line(f"Choose 1-{len(options)}{hint}: ").strip()
        if not answer and keep:
            return current
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        say(f"Type a number from 1 to {len(options)}.")


def secret_twice(label: str, minimum: int = 1) -> str:
    while True:
        first = getpass.getpass(f"  {label}: ")
        if not first:
            return ""
        if len(first) < minimum:
            say(f"At least {minimum} characters.")
            continue
        if getpass.getpass("  Again, to check: ") == first:
            return first
        say("The two did not match. Once more.")


def bytes_text(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1000
    return ""


class Engine:
    """The DoubleTake engine the screens use: the running one, or one started here for the setup."""

    def __init__(self, cfg: dict):
        self.port = int(cfg.get("port", 8765))
        self.base = f"http://127.0.0.1:{self.port}"
        self.password = ""
        self._stop = None
        from .server import _already_running

        if _already_running(self.port):
            return
        from http.server import ThreadingHTTPServer

        from .server import make_handler
        from .station import Station

        station = Station(cfg, "camera")
        httpd = ThreadingHTTPServer(("127.0.0.1", self.port), make_handler(station, lambda: None))
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, name="setup-engine", daemon=True).start()
        station.start()

        def stop():
            httpd.shutdown()
            station.shutdown()
            httpd.server_close()

        self._stop = stop

    def close(self):
        if self._stop:
            self._stop()
            self._stop = None

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=30) as reply:
            return json.load(reply)

    def post(self, path: str, data: dict | None = None, label: str = "") -> dict:
        body = dict(data or {})
        for attempt in range(3):
            if self.password:
                body["password"] = self.password
            req = urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json", "X-DoubleTake": "1"})
            try:
                with urllib.request.urlopen(req, timeout=120) as reply:
                    answer = json.load(reply)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                return {"ok": False, "error": f"DoubleTake did not answer: {exc}"}
            if not answer.get("need_password") or attempt == 2:
                return answer
            if self.password:
                say(answer.get("error") or "That was not the DoubleTake password.")
            self.password = getpass.getpass(f"  DoubleTake password{' for ' + label if label else ''}: ")
            if not self.password:
                return {"ok": False, "error": "No password given, so nothing was changed."}
        return {"ok": False, "error": "Not changed."}

    def settings(self) -> dict:
        return self.get("/api/settings")

    def state(self) -> dict:
        return self.get("/api/state")

    def change(self, **changes) -> bool:
        answer = self.post("/api/settings", {"changes": changes}, "Settings")
        if answer.get("error"):
            warn("Not saved: " + answer["error"])
            return False
        return True

    def nas_done(self) -> str:
        """Wait for a NAS job (login, switch, firewall) to finish. Returns its error, or ''."""
        time.sleep(0.5)
        for _ in range(240):
            job = self.state().get("nas_job") or {}
            if not job.get("busy"):
                return job.get("error") or ""
            time.sleep(0.5)
        return "It is taking a long time. Look in Settings, NAS, later."

    def trust(self, **changes) -> bool:
        answer = self.post("/api/nas/trust", {"changes": changes})
        error = answer.get("error") or self.nas_done()
        if error:
            warn("Not saved: " + error)
            return False
        return True


def _is_admin() -> bool:
    if os.geteuid() == 0:
        return True
    try:
        names = {grp.getgrgid(g).gr_name for g in os.getgroups()}
    except KeyError:
        names = set()
    return bool(names & {"wheel", "sudo", "admin"})


def system_step(cfg: dict, alone: bool = False) -> bool:
    """Step 1: the once-only system part. True when the tablet is set up (or the person chose
    to go on without it)."""
    if alone:
        print()
    else:
        heading(1, "Welcome to DoubleTake",
                "Two verified copies of every photo, and Safe to Format only when it is true. A few choices to make "
                "first, about two minutes. Every one of them can be changed later in Settings.")
    if not firstrun.packaged():
        good("This DoubleTake was installed by setup.sh, which does the system part: if something is missing, "
             "run setup.sh again.")
        return True
    if not firstrun.needs_system_setup():
        good("The tablet is set up for DoubleTake.")
        return True
    say("First, once only: the built-in NAS, the boot screen, the boot menu entry, and what lets the screens "
        "switch them on and off.")
    print()
    if not _is_admin() or not shutil.which("sudo"):
        warn(f"{getpass.getuser()} is not an administrator on this tablet, so it cannot do this part.")
        say(f"Make it one, then log out and back in, and run doubletake setup again:  "
            f"su -c 'usermod -aG wheel {getpass.getuser()}'")
        return False
    if subprocess.run(["sudo", "-v", "-p", "  Password for %u (the tablet's login): "]).returncode != 0:
        warn("sudo did not take the password. Nothing was changed.")
        return False
    library = str(cfg["library"])
    box = {}
    worker = threading.Thread(target=lambda: box.update(error=firstrun.system_setup(library)), daemon=True)
    started = time.monotonic()
    worker.start()
    while worker.is_alive():
        worker.join(1)
        if TTY:
            spent = int(time.monotonic() - started)
            print(f"\r  Setting up the tablet. The boot screen takes a few minutes.  {spent // 60}:{spent % 60:02d} ",
                  end="", flush=True)
    if TTY:
        print()
    error = box.get("error", "The system setup did not finish.")
    if error:
        warn("Not set up: " + error)
        if alone:
            return False
        print()
        say("Until it is, Copy 2 drives cannot be prepared, and the NAS, the boot screen and the start-up "
            "switches do not work.")
        return not yes("Stop here, to fix it and run doubletake setup again?", True)
    good("The tablet is set up: the built-in NAS, the boot screen, the boot menu entry and the switches.")
    if alone:
        say("Restart the tablet to see the boot screen. The other questions: doubletake setup")
    return True


def password_step(dt: Engine) -> None:
    heading(2, "The DoubleTake password",
            "Separate from the tablet's own login. It guards the ways out of DoubleTake (Maintenance mode, Exit) "
            "and the jobs that cannot be undone (preparing a drive, Format, Reset, Erase). Everyday backups never "
            "ask for it.")
    pw = dt.settings().get("fs_password") or {}
    if not pw.get("available"):
        warn("Needs the system setup first. It can be set later in Settings, Device.")
        return
    mode = pw.get("mode")
    if mode == "password":
        good("Set.")
        if not yes("Change it?", False):
            return
        current = getpass.getpass("  The password now: ")
    else:
        if mode == "none":
            say("No password now: those jobs ask you to confirm, but anyone can confirm.")
        choice = choose([("set", "Set a password"), ("none", "No password")], "none" if mode == "none" else None)
        if choice == "none":
            if mode != "none":
                answer = dt.post("/api/fs-password/none", {"current": ""})
                if answer.get("error"):
                    warn(answer["error"])
                    return
            good("No password.")
            return
        current = ""
    while True:
        new = secret_twice("New DoubleTake password (Enter to leave it)")
        if not new:
            say("Left as it was.")
            return
        answer = dt.post("/api/fs-password/set", {"current": current, "new": new})
        if not answer.get("error"):
            dt.password = new
            good("Password set.")
            return
        warn(answer["error"])
        if mode == "password":
            return


def copy_step(dt: Engine) -> None:
    heading(3, "What to copy",
            "Which files come off the camera. Whatever is left on the camera keeps its cards from being called "
            "safe to format, so nothing is lost by accident.")
    now = dt.settings().get("copy_what")
    picked = choose([("raw", "RAW only"), ("raw_jpeg", "RAW + JPEG (no videos)"), ("all", "Everything, videos too")], now)
    if picked != now:
        dt.change(copy_what=picked)
    good({"raw": "RAW files only.", "raw_jpeg": "RAW files and JPEGs.", "all": "Every photo and video."}[picked])


def _prepare(dt: Engine, card: dict) -> bool:
    if card.get("noun") == "folder":
        answer = dt.post("/api/card/use")
        return not answer.get("error")
    d = dt.post("/api/card/details")
    word = "drive" if card.get("kind") == "usb" else "card"
    what = d.get("name") or f"the {word}"
    size = bytes_text(d.get("size") or card.get("total"))
    parts = ", ".join(("'" + p["label"] + "'" if p.get("label") else "unnamed") + f" ({p.get('fstype') or 'unknown'}, "
                      + bytes_text(p.get("size")) + ")" for p in d.get("partitions") or [])
    print()
    say(bold(f"New {word}: {what}, {size}") + (f"\nOn it now: {parts}" if parts else ""))
    choice = choose([("erase", "Erase it and keep it for DoubleTake's Copy 2"), ("ignore", f"Not this {word}"),
                     ("later", "Decide later")], None)
    if choice == "ignore":
        dt.post("/api/card/ignore")
        say(f"This {word} is left as it is.")
        return False
    if choice == "later":
        return False
    warn(f"Everything on {what} ({size}) will be erased. It cannot be brought back. Afterwards it holds "
         "DoubleTake's second copy, and nothing else.")
    agree = line(f"Type AGREE to erase {what}: ").strip()
    if agree.upper() != "AGREE":
        say(f"AGREE was not typed, so {what} was left as it is.")
        return False
    say("Preparing. This takes a moment.")
    answer = dt.post("/api/card/prepare", {"agree": agree}, f"erasing {what}")
    if answer.get("error"):
        warn("Not prepared: " + answer["error"])
        return False
    good(f"{what} is now DoubleTake's Copy 2. The second copy starts by itself.")
    return True


def second_step(dt: Engine) -> None:
    heading(4, "Copy 2",
            "The second copy lives on a card or drive of its own: the microSD card in the tablet's slot, or a USB "
            "drive. A new one is erased and kept for DoubleTake, after you confirm. Until there is a Copy 2, "
            "backups exist only on the tablet.")
    now = dt.settings().get("second_copy_device")
    picked = choose([("microsd", "The microSD card in the tablet's slot"), ("usb", "A USB drive")], now)
    if picked != now:
        dt.change(second_copy_device=picked)
        time.sleep(2)
    word = "drive" if picked == "usb" else "card"
    while True:
        card = dt.state().get("card") or {}
        state = card.get("state")
        if state == "ready":
            label = card.get("label") or ""
            good(f"Ready: {'' if label in ('', 'DT-COPY2') else repr(label) + ', '}"
                 f"{bytes_text(card.get('free'))} free of {bytes_text(card.get('total'))}.")
            return
        if state == "new":
            if _prepare(dt, card):
                return
            return
        if state == "declined":
            if yes(f"The {word} that is in was turned down before. Use it now?", False):
                dt.post("/api/card/use")
                time.sleep(2)
                continue
            return
        if state == "several":
            warn("More than one drive is plugged in. Unplug all but one.")
        elif state == "readonly":
            warn(f"The {word} is read-only, so it cannot hold the second copy.")
        elif card.get("unopened"):
            warn(f"A {word} is in, but the system has not opened it. Open Files and select it.")
        else:
            say(f"No {word} yet." + (" Plug in the USB drive." if picked == "usb" else " Put a microSD card in the tablet."))
        if line(f"Press Enter once the {word} is in, or type s to skip for now: ").strip().lower().startswith("s"):
            say("Later, then: DoubleTake asks about it when it goes in.")
            return
        say("Looking…")
        time.sleep(4)


def nas_step(dt: Engine) -> None:
    heading(5, "The built-in NAS",
            "At home, use DoubleTake's built-in NAS to bring the photos onto your editing workstation, over Wi-Fi or "
            "a network cable. Any photo app that imports from a network folder can read it. Read-only: nothing on "
            "the workstation can change or delete the photos.")
    nas = dt.state().get("nas") or {}
    if not nas.get("installed"):
        warn("Needs the system setup first. It can be switched on later in Settings, NAS.")
        return
    say(bold("1. Login for the NAS"))
    if nas.get("user"):
        say(f"Your editing workstation logs in as {nas['user']}.")
        change = yes("Change the login?", False)
    else:
        say("A name and password for the NAS alone: your workstation uses them to connect. They are not the "
            "tablet's own login and cannot unlock the tablet.")
        change = True
    while change:
        name = line("NAS login name (small letters and digits, starting with a letter; Enter to skip): ").strip()
        if not name:
            break
        password = secret_twice("NAS password")
        if not password:
            break
        answer = dt.post("/api/nas/login", {"name": name, "password": password})
        error = answer.get("error") or dt.nas_done()
        if error:
            warn(error)
            continue
        nas = dict(nas, user=name)
        good(f"Your workstation logs in as {name}.")
        break
    print()
    say(bold("2. Share the photos"))
    if not nas.get("user"):
        say("Needs the login first. It can be switched on later in Settings, NAS.")
        return
    on = yes("Share the photos on the networks you trust (next step)?", bool(nas.get("nas_on")))
    if on != bool(nas.get("nas_on")):
        answer = dt.post("/api/nas/switch", {"what": "nas", "on": on})
        error = answer.get("error") or dt.nas_done()
        if error:
            warn(error)
            return
    good("On." if on else "Off.")


def networks_step(dt: Engine) -> None:
    heading(6, "Where the share opens", "Only on networks you trust: never on hotel, airport or café Wi-Fi.")
    if not (dt.state().get("nas") or {}).get("installed"):
        warn("Needs the system setup first. It can be chosen later in Settings, NAS.")
        return
    prefs = dt.settings()
    say(bold("A network cable"))
    say("Plug a network cable from the tablet straight into your editing workstation, with no router needed: the "
        "two find each other in about 20 seconds. This is FSDC, DoubleTake's direct connection, and the fastest "
        "way to bring a big shoot home. It also works through a router at home.")
    cable = yes("Open the share over a cable?", bool(prefs.get("nas_cable")))
    print()
    say(bold("The tablet's hotspot"))
    say("Its own Wi-Fi network, made by the tablet.")
    hotspot = yes("Open the share on the hotspot?", bool(prefs.get("nas_hotspot")))
    changes = {}
    if cable != bool(prefs.get("nas_cable")):
        changes["nas_cable"] = cable
    if hotspot != bool(prefs.get("nas_hotspot")):
        changes["nas_hotspot"] = hotspot
    ssid = (dt.state().get("net") or {}).get("ssid")
    trusted = list(prefs.get("nas_trusted") or [])
    print()
    if ssid:
        say(bold(f"This Wi-Fi: {ssid}"))
        say("Trust it only if it is your own, at home.")
        on = yes(f"Trust {ssid}?", ssid in trusted)
        if on != (ssid in trusted):
            changes["nas_trusted"] = trusted + [ssid] if on else [v for v in trusted if v != ssid]
    else:
        say("To trust your home Wi-Fi, join it in Settings, Network, then switch it on in Settings, NAS.")
    if changes and dt.trust(**changes):
        good("Saved.")


def hotspot_step(dt: Engine) -> None:
    heading(7, "The hotspot", "The tablet can make a Wi-Fi network of its own, for a phone or a laptop in the field.")
    prefs = dt.settings()
    name = line(f"Hotspot name [Enter keeps {prefs.get('hotspot_name')}]: ").strip()
    if name and name != prefs.get("hotspot_name"):
        if len(name) > 32:
            warn("At most 32 characters; the name was left as it was.")
        else:
            dt.change(hotspot_name=name)
    say("Password: " + ("set." if prefs.get("hotspot_password") else "not set yet, at least 8 characters."))
    pw = secret_twice("Hotspot password (Enter to " + ("keep it" if prefs.get("hotspot_password") else "leave it for later") + ")", 8)
    if pw:
        if len(pw) > 63:
            warn("At most 63 characters; the password was left as it was.")
        elif dt.change(hotspot_password=pw):
            good("Hotspot password set.")


def poweron_step(dt: Engine) -> None:
    heading(8, "At power-on",
            "How the tablet starts. For a tablet that lives in a camera bag, all on: power on, and DoubleTake is ready.")
    prefs = dt.settings()
    say(bold("Start at power-on"))
    if not prefs.get("power_on_ready"):
        warn("Needs the system setup first.")
    else:
        say("Logs in by itself, with no password, and opens DoubleTake. Anyone who picks up the tablet can then use "
            "it without a password; the share keeps its own login.")
        on = yes("Start at power-on?", bool(prefs.get("start_at_power_on")))
        if on != bool(prefs.get("start_at_power_on")):
            dt.change(start_at_power_on=on)
    print()
    say(bold("Boot screen"))
    if not prefs.get("splash_available"):
        warn("Needs the system setup first.")
    else:
        say("The DoubleTake icon while the tablet starts, inside a turning orange and green ring.")
        on = yes("Show the boot screen?", bool(prefs.get("splash_on")))
        if on != bool(prefs.get("splash_on")):
            dt.change(boot_splash=on)
    print()
    say(bold("DoubleTake only"))
    if not prefs.get("kiosk_available"):
        warn("Needs the system setup first.")
    else:
        say("No desktop behind it: the tablet starts straight into DoubleTake. Maintenance mode gets the desktop "
            "back for one visit.")
        on = yes("DoubleTake only?", bool(prefs.get("kiosk_on")))
        if on != bool(prefs.get("kiosk_on")):
            dt.change(kiosk=on)


def finish_step(dt: Engine) -> int:
    heading(9, "All set",
            "Restarting brings the tablet up as set up: the boot screen, then DoubleTake. After that, plug in the "
            "camera to start a backup. Everything chosen here is in Settings.")
    for i in range(450):
        if not (dt.settings().get("splash_job") or {}).get("busy"):
            break
        if TTY:
            print(f"\r  Building the boot screen…  {i * 2 // 60}:{i * 2 % 60:02d} ", end="", flush=True)
        time.sleep(2)
    else:
        print()
    if not yes("Restart the tablet now?", True):
        good("Done. Restart when it suits you.")
        return 0
    answer = dt.post("/api/leave", {"action": "restart"})
    error = answer.get("error") or ""
    if not error:
        # The way out runs behind the reply, step by step: wait for it to say how it went.
        for _ in range(120):
            leaving = dt.state().get("leaving") or {}
            if leaving.get("done"):
                error = leaving.get("error") or ""
                break
            time.sleep(0.5)
        if not error:
            say("Restarting.")
            time.sleep(30)  # the restart is under way; this ends with the tablet
            error = "the tablet did not restart"
    if "inhibitor" in error:
        # The terminal holds off a restart while it is running a program (this setup). Newer
        # systems no longer let even root override that, so the restart is asked for a few
        # seconds on, and this setup ends: by then the terminal has let go, and nothing is
        # overridden. sudo was given its password above.
        dt.close()
        done = subprocess.run(["sudo", "systemd-run", "--quiet", "--on-active=5", "--timer-property=AccuracySec=1s",
                               "systemctl", "reboot"])
        if done.returncode == 0:
            say("Restarting in 5 seconds.")
            return 0
        error = "the system did not restart"
    if error:
        warn("Could not restart: " + error + ". Restart from the desktop's menu, or type: reboot")
        return 1
    say("Restarting.")
    return 0


def run(cfg: dict) -> int:
    print()
    print("  " + bold(f"DoubleTake {version_text(__version__)}") + dim("  ·  setup"))
    print(dim("  Enter keeps what is shown. Ctrl-C stops; what was answered is kept."))
    dt = None
    try:
        if not system_step(cfg):
            return 1
        dt = Engine(cfg)
        for _ in range(30):
            try:
                dt.state()
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)
        for step in (password_step, copy_step, second_step, nas_step, networks_step, hotspot_step, poweron_step):
            step(dt)
        return finish_step(dt)
    except KeyboardInterrupt:
        print()
        say("Stopped. What was answered is kept. To carry on: doubletake setup")
        return 130
    finally:
        if dt:
            dt.close()
