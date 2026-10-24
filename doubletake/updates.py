"""Updates, offered in Maintenance: the system's own, and a newer DoubleTake.

Maintenance is the one time the desktop is up and DoubleTake is closed, so nothing is
being copied while packages change. When the tablet is due a look (every 30 days by
default, Settings, Device), or a security fix is waiting, a terminal opens with
`doubletake update`, which asks before it checks, asks again before it installs, and
offers a restart when one is needed. In the field nothing is ever looked for.

  doubletake update           the questions, now
  doubletake update --auto    run at a maintenance login: open the terminal only when due

The system's updates come from its own software sources (dnf). DoubleTake's come from
its releases on GitHub: the package for this system, install.sh and the checksums,
downloaded into ~/Downloads/DoubleTake-<version>, checked, and installed with install.sh.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__, config, nas
from .util import version_text

REPO = "nickmackowski/doubleTAKE"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
DAYS = (30, 60, 90)  # or 0: off, never looked for at a maintenance visit


def state_path() -> Path:
    return config.config_path().parent / "updates.json"


def _load() -> dict:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def off(cfg: dict) -> bool:
    return cfg.get("update_days") == 0 and not isinstance(cfg.get("update_days"), bool)


def due(cfg: dict, now: float | None = None) -> bool:
    if off(cfg):
        return False
    days = cfg.get("update_days", 30) if cfg.get("update_days") in DAYS else 30
    last = float(_load().get("checked", 0) or 0)
    return (now or time.time()) - last >= days * 86400


def mark_checked(now: float | None = None) -> None:
    data = _load()
    data["checked"] = now or time.time()
    _save(data)


# ----- comparing versions, as the package manager does --------------------------------------


def vercmp(a: str, b: str) -> int:
    """-1, 0 or 1, like rpm: numbers by value, letters by letter, and ~ sorting before
    anything (1.0~pr1 < 1.0 < 1.0c < 1.0.1)."""
    a, b = a or "", b or ""
    while a or b:
        a = re.sub(r"^[^A-Za-z0-9~]+", "", a)
        b = re.sub(r"^[^A-Za-z0-9~]+", "", b)
        if a.startswith("~") or b.startswith("~"):
            if not a.startswith("~"):
                return 1
            if not b.startswith("~"):
                return -1
            a, b = a[1:], b[1:]
            continue
        if not a or not b:
            break
        if a[0].isdigit():
            ma, mb = re.match(r"\d+", a), re.match(r"\d+", b)
            if not mb:
                return 1  # a number beats letters
            x, y = int(ma.group()), int(mb.group())
            if x != y:
                return 1 if x > y else -1
        else:
            ma, mb = re.match(r"[A-Za-z]+", a), re.match(r"[A-Za-z]+", b)
            if not mb:
                return -1
            if ma.group() != mb.group():
                return 1 if ma.group() > mb.group() else -1
        a, b = a[ma.end():], b[mb.end():]
    if not a and not b:
        return 0
    return 1 if a else -1


# ----- DoubleTake's own releases -------------------------------------------------------------


def dist() -> str:
    """'el9' or 'fc': which of the release's packages is for this system."""
    try:
        text = Path("/etc/os-release").read_text()
    except OSError:
        return ""
    ident = dict(re.findall(r'^(ID)="?([^"\n]*)"?', text, re.M)).get("ID", "")
    return "fc" if ident == "fedora" else "el9"


def latest_release(opener=urllib.request.urlopen) -> dict:
    """The newest release on GitHub: {'version', 'assets': {name: url}} or {'error'}."""
    try:
        req = urllib.request.Request(LATEST, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": "DoubleTake"})
        with opener(req, timeout=20) as reply:
            data = json.load(reply)
    except urllib.error.HTTPError as exc:
        return {"error": "no public release found" if exc.code == 404 else f"GitHub answered {exc.code}"}
    except Exception as exc:
        return {"error": f"GitHub did not answer ({exc})"}
    tag = str(data.get("tag_name") or "").lstrip("vV")
    assets = {a.get("name", ""): a.get("browser_download_url", "") for a in data.get("assets") or []}
    return {"version": tag, "assets": assets, "notes": str(data.get("body") or "")}


def pick_assets(assets: dict, system: str) -> dict:
    """The files this tablet needs from a release: its package, install.sh, the checksums."""
    out = {}
    for name, url in assets.items():
        if name.endswith(".rpm") and f".{system}." in name and name.startswith("doubletake-"):
            out["rpm"] = (name, url)
        elif name == "install.sh":
            out["install"] = (name, url)
        elif name.upper() in ("SHA256SUMS", "SHA256SUMS.TXT"):
            out["sums"] = (name, url)
    return out


def check_sums(folder: Path, sums_text: str, names) -> str:
    """Every downloaded file against the release's checksums. Returns a problem, or ''."""
    listed = {}
    for row in sums_text.splitlines():
        parts = row.split()
        if len(parts) >= 2:
            listed[parts[-1].lstrip("*")] = parts[0].lower()
    for name in names:
        want = listed.get(name)
        if not want:
            return f"{name} is not in the checksums"
        h = hashlib.sha256()
        with open(folder / name, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        if h.hexdigest() != want:
            return f"{name} does not match its checksum"
    return ""


# ----- the terminal ----------------------------------------------------------------------------


def open_terminal(command: list) -> bool:
    """Open the desktop's own terminal running a command. False when there is none."""
    for args in (["ptyxis", "--"], ["gnome-terminal", "--"], ["kgx", "--"], ["xterm", "-e"]):
        if shutil.which(args[0]):
            try:
                subprocess.Popen(args + command, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except OSError:
                continue
    return False


def auto(cfg: dict) -> int:
    """At a maintenance login: open the terminal when the tablet is due a look, or when a
    security fix is waiting. Otherwise nothing, silently."""
    command = [shutil.which("doubletake") or "doubletake", "update"]
    if off(cfg):
        return 0  # switched off: not even security fixes are looked for (doubletake update still works)
    if due(cfg):
        open_terminal(command)
        return 0
    found = nas.helper("updates", "check", timeout=900)
    if found.get("ok") and int(found.get("security") or 0) > 0:
        open_terminal(command + ["--security"])
    return 0


def _say(text: str = "") -> None:
    print("  " + text if text else "")


def _yes(question: str, default: bool) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"  {question} {hint} ").strip().lower()
    except EOFError:
        return False
    return default if not answer else answer.startswith("y")


def _sudo() -> bool:
    return subprocess.run(["sudo", "-v", "-p", "  Password for %u (the tablet's login): "]).returncode == 0


def _download(url: str, target: Path, opener=urllib.request.urlopen) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "DoubleTake"})
    with opener(req, timeout=60) as reply, open(target, "wb") as fh:
        shutil.copyfileobj(reply, fh, 1 << 20)


def run(cfg: dict, security: bool = False) -> int:
    """The questions, in the terminal."""
    print()
    _say(f"DoubleTake {version_text(__version__)}  ·  updates")
    print()
    if security:
        _say("Security fixes are waiting for this system.")
    elif not _yes("Check for updates now?", True):
        _say("Not now. You are asked again at the next Maintenance visit.")
        return _close(0)
    restart = False

    # 1. The system's own updates.
    _say("Looking for system updates…")
    found = nas.helper("updates", "check", timeout=900)
    if not found.get("ok"):
        _say("Could not check: " + found.get("error", "no answer").rstrip(".") + ".")
    elif not found.get("count"):
        _say("The system is up to date.")
    else:
        n, sec = int(found["count"]), int(found.get("security") or 0)
        _say(f"{n} update{'s' if n != 1 else ''} waiting"
             + (f", {sec} of them security fix{'es' if sec != 1 else ''}" if sec else "")
             + (", with a new kernel" if found.get("kernel") else "") + ".")
        if _yes("Install them now?", False):
            if _sudo() and subprocess.run(["sudo", "dnf", "upgrade", "-y"]).returncode == 0:
                _say("Installed.")
                done = subprocess.run(["sudo", "dnf", "-q", "needs-restarting", "-r"], capture_output=True)
                restart = done.returncode == 1 or bool(found.get("kernel"))
            else:
                _say("The updates were not installed.")
    print()

    # 2. A newer DoubleTake.
    _say("Looking for a newer DoubleTake…")
    release = latest_release()
    if release.get("error"):
        _say("Could not check: " + release["error"].rstrip(".") + ".")
    elif vercmp(release["version"], __version__) <= 0:
        _say(f"DoubleTake {version_text(__version__)} is the newest.")
    else:
        files = pick_assets(release["assets"], dist())
        _say(f"DoubleTake {version_text(release['version'])} is out (this tablet has {version_text(__version__)}).")
        if "rpm" not in files or "install" not in files:
            _say("It has no package for this system yet.")
        elif _yes("Download and install it?", True):
            restart = _install_release(release, files) or restart
    mark_checked()
    print()
    if restart and _yes("Restart the tablet now, to finish? It comes up in DoubleTake.", True):
        # Asked for a few seconds on: by then this terminal has closed and holds nothing off.
        subprocess.run(["sudo", "systemd-run", "--quiet", "--on-active=5", "--timer-property=AccuracySec=1s",
                        "systemctl", "reboot"])
        _say("Restarting in 5 seconds.")
        return 0
    return _close(0)


def _install_release(release: dict, files: dict) -> bool:
    folder = Path(os.path.expanduser("~/Downloads")) / f"DoubleTake-{release['version']}"
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    try:
        for key in ("rpm", "install", "sums"):
            if key in files:
                name, url = files[key]
                _say(f"Downloading {name}…")
                _download(url, folder / name)
                names.append(name)
    except Exception as exc:
        _say(f"The download did not finish ({exc}). Nothing was installed.")
        return False
    if "sums" in files:
        problem = check_sums(folder, (folder / files["sums"][0]).read_text(), [files["rpm"][0], files["install"][0]])
        if problem:
            _say(f"Not installed: {problem}. The files are in {folder}.")
            return False
        _say("Checked against the release's checksums.")
    elif not _yes("This release has no checksums to check the files against. Install anyway?", False):
        return False
    done = subprocess.run(["bash", str(folder / files["install"][0])], cwd=folder)
    return done.returncode == 0


def _close(code: int) -> int:
    if sys.stdin.isatty():
        try:
            input("  Press Enter to close. ")
        except EOFError:
            pass
    return code
