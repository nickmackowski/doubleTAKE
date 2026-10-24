"""The tablet as a NAS: the photos folder shared read-only over Samba, and SFTP.

The jobs that need root are done by the system helper that setup.sh installs
(/usr/local/libexec/doubletake-helper), which the user may run through sudo
without a password. Everything else here only reads.

Where the share may be seen is decided by the firewall, per network:
trusted networks (the cable, the hotspot, Wi-Fi networks switched on in the
settings) are put in the firewall's "home" zone, where the share is open,
and every other network in "public", where it is closed. That holds even
when the app is not running.
"""

from __future__ import annotations

import ipaddress
import json
import threading
import shutil
import re
import socket
import subprocess
import time
from pathlib import Path

from . import network

# /usr/libexec when installed from the package (RPM), /usr/local/libexec by setup.sh.
HELPER = next((p for p in ("/usr/libexec/doubletake-helper", "/usr/local/libexec/doubletake-helper")
               if Path(p).is_file()), "/usr/local/libexec/doubletake-helper")
STATE_FILE = Path("/etc/doubletake/nas.json")
STATUS_FILE = Path("/run/doubletake/nas-status.json")
SFTP_PORT = 2222


def installed() -> bool:
    try:
        return Path(HELPER).is_file() and STATE_FILE.is_file()
    except OSError:  # a folder the screens may not look into: set up, but not readable
        return False


def helper(*args, stdin: str | None = None, timeout: float = 300) -> dict:
    """Run one of the helper's jobs. Returns its answer, with 'error' when it failed."""
    if not Path(HELPER).is_file():
        return {"ok": False, "error": "The NAS is not set up yet. Run doubletake setup in a terminal; it asks for your password once."}
    try:
        done = subprocess.run(["sudo", "-n", HELPER] + list(args), input=stdin, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "The system took too long to answer."}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        answer = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        err = done.stderr.strip()
        if "password is required" in err or "a terminal is required" in err:
            err = "The NAS helper is not allowed to run. Run doubletake setup in a terminal."
        return {"ok": False, "error": err or "The NAS helper gave no answer."}
    if not answer.get("ok"):
        answer.setdefault("error", "The NAS helper refused.")
    return answer


def system_state() -> dict:
    """What the helper has set up: the owner, the folder, the share's login. No passwords."""
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _active(unit: str) -> bool:
    try:
        out = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5).stdout
        return out.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


# ----- computers by name, not by address ---------------------------------------------
#
# The status service sees each computer by its address (often a long IPv6 one from a Mac).
# Its name is asked for once, in the background, from the network's own name service: first
# mDNS (the avahi service the desktop runs; a Mac answers as Nicks-MacBook-Pro.local), then
# the router's names. Until an answer comes, or if none does, the address is shown.

_names: dict = {}           # address -> (name or "", when asked)
_names_lock = threading.Lock()
NAME_RETRY = 300            # seconds before asking again about an address with no name


def _bare(address: str) -> str:
    address = (address or "").strip()
    for prefix in ("ipv4:", "ipv6:"):
        if address.startswith(prefix):
            address = address[len(prefix):]
    if address.count(":") == 1:  # ipv4:port
        address = address.split(":")[0]
    return address.strip("[]").split("%")[0]


def is_address(text: str) -> bool:
    try:
        ipaddress.ip_address(_bare(text))
        return True
    except ValueError:
        return False


def _tidy(name: str) -> str:
    name = name.strip().rstrip(".")
    for tail in (".local", ".lan", ".home", ".localdomain", ".home.arpa"):
        if name.lower().endswith(tail):
            name = name[: -len(tail)]
    return name


def _ask_mdns(address: str) -> str:
    exe = shutil.which("gdbus")
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "call", "--system", "--dest", "org.freedesktop.Avahi", "--object-path", "/",
                              "--method", "org.freedesktop.Avahi.Server.ResolveAddress", "--", "-1", "-1",
                              address, "0"], capture_output=True, text=True, timeout=6).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    found = re.findall(r"'([^']+)'", out)
    return found[1] if len(found) >= 2 else ""


def _ask(address: str) -> None:
    name = _ask_mdns(address)
    if not name:
        try:
            name = socket.gethostbyaddr(address)[0]
        except (OSError, UnicodeError):
            name = ""
    if name and is_address(name):
        name = ""
    with _names_lock:
        _names[address] = (_tidy(name), time.time())


def computer_name(address: str) -> str:
    """The computer's name, if known yet; asks in the background when not."""
    address = _bare(address)
    with _names_lock:
        known = _names.get(address)
        if known and (known[0] or time.time() - known[1] < NAME_RETRY):
            return known[0]
        _names[address] = ("", time.time())  # asking
    threading.Thread(target=_ask, args=(address,), name="name-lookup", daemon=True).start()
    return ""


def neighbours(text: str | None = None) -> dict:
    """Address -> network card (MAC), from the tablet's own table of nearby machines.

    A Mac's IPv4 address and its temporary IPv6 one lead to the same card, which is how
    two connections are known to be one computer, and how an IPv6 address gets a name.
    """
    if text is None:
        exe = shutil.which("ip")
        if not exe:
            return {}
        try:
            text = subprocess.run([exe, "neigh"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if "lladdr" in parts and parts:
            mac = parts[parts.index("lladdr") + 1].lower() if parts.index("lladdr") + 1 < len(parts) else ""
            if mac and "FAILED" not in parts:
                out[_bare(parts[0])] = mac
    return out


def _name_by_card(address: str, cards: dict) -> str:
    """A name for an address with none of its own: from another address on the same card."""
    mac = cards.get(_bare(address))
    if not mac:
        return ""
    for other, other_mac in cards.items():
        if other_mac == mac and other != _bare(address):
            name = computer_name(other)
            if name:
                return name
    return ""


def connections(max_age: float = 15.0) -> dict:
    """Who is connected, and which files are open, as last written by the status service."""
    try:
        data = json.loads(STATUS_FILE.read_text())
    except (OSError, ValueError):
        return {"sessions": [], "open": [], "fresh": False}
    data["fresh"] = time.time() - float(data.get("time", 0)) < max_age
    if not data["fresh"]:
        data["sessions"], data["open"] = [], []
    cards = None
    for s in data.get("sessions", []):
        seen = s.get("machine") or s.get("address") or ""
        if seen and is_address(seen):
            name = computer_name(seen)
            if not name:
                cards = neighbours() if cards is None else cards
                name = _name_by_card(seen, cards)
            if name:
                s["machine"] = name
    data["sessions"] = merge_sessions(data.get("sessions", []))
    return data


def merge_sessions(sessions: list) -> list:
    """One row per computer: a Mac on the share over IPv6 and on SFTP over IPv4 is one computer."""
    out, by_name = [], {}
    for s in sessions:
        name = s.get("machine") or ""
        key = name.lower() if name and not is_address(name) else None
        if key and key in by_name:
            row = by_name[key]
            if s.get("kind") != row.get("kind"):
                row["kind"] = "both"
            row["user"] = row.get("user") or s.get("user", "")
            continue
        row = dict(s)
        out.append(row)
        if key:
            by_name[key] = row
    return out


# ----- where the tablet can be reached ---------------------------------------------


def addresses() -> list[dict]:
    """This tablet's own addresses, each with the kind of network it is on."""
    found = []
    try:
        # Not only global ones: the direct cable's address is link-local (169.254.x.x).
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return found
    kinds = {d["device"]: d for d in network.devices()}
    for line in out.splitlines():
        match = re.match(r"^\d+:\s+(\S+)\s+inet\s+([0-9.]+)/", line)
        if not match:
            continue
        device, address = match.group(1), match.group(2)
        if device == "lo" or address.startswith("127."):
            continue
        info = kinds.get(device, {})
        kind = "cable" if info.get("type") == "ethernet" else "wifi" if info.get("type") == "wifi" else "other"
        if info.get("connection") == network.HOTSPOT:
            kind = "hotspot"
        elif info.get("connection") == "DoubleTake-Direct":
            kind = "direct"
        found.append({"device": device, "address": address, "kind": kind, "network": info.get("connection", "")})
    return found


def host_name() -> str:
    return socket.gethostname().split(".")[0]


# ----- trusted networks ----------------------------------------------------------------


def known_networks() -> list[dict]:
    """The tablet's saved Wi-Fi and cable networks, with whether each has a password and its zone."""
    code, out, _ = network._run(["-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show"], timeout=10)
    if code != 0:
        return []
    found = []
    for row in network._rows(out):
        if len(row) < 4 or row[0] in (network.HOTSPOT, "DoubleTake-Direct"):
            continue
        name, uuid, kind, device = row[0], row[1], row[2], row[3]
        if kind not in ("802-11-wireless", "802-3-ethernet"):
            continue
        code, detail, _ = network._run(["-t", "-f", "connection.zone,802-11-wireless-security.key-mgmt",
                                        "connection", "show", "uuid", uuid], timeout=10)
        fields = dict(line.split(":", 1) for line in detail.splitlines() if ":" in line) if code == 0 else {}
        found.append({
            "name": name, "uuid": uuid, "device": device,
            "kind": "cable" if kind == "802-3-ethernet" else "wifi",
            "zone": fields.get("connection.zone", "").strip(),
            "secured": bool(fields.get("802-11-wireless-security.key-mgmt", "").strip()) if kind != "802-3-ethernet" else True,
        })
    return found


def wanted_zone(net: dict, cfg: dict) -> str:
    """The firewall zone a network should be in, by the settings."""
    if net["kind"] == "cable":
        return "home" if cfg.get("nas_cable", True) else "public"
    if net["secured"] and net["name"] in (cfg.get("nas_trusted") or []):
        return "home"
    return "public"


def sync_zones(cfg: dict, nets: list[dict] | None = None) -> list[str]:
    """Put each known network in the zone the settings want. Returns errors, if any.

    A network left with no zone set counts as public, so it is only changed when it
    should be trusted, or when it was trusted and should not be any more.
    """
    errors = []
    for net in nets if nets is not None else known_networks():
        want = wanted_zone(net, cfg)
        have = net["zone"] or "public"
        if have == want or (want == "public" and have not in ("home",)):
            continue
        extra = [net["device"]] if net["device"] else []
        answer = helper("zone", net["uuid"], want, *extra)
        if not answer.get("ok"):
            errors.append(f"{net['name']}: {answer.get('error')}")
    return errors


_SECURED: dict = {}


def _secured(connection: str) -> bool:
    """Whether a saved Wi-Fi network has a password. Remembered for a minute; it is asked every tick."""
    known = _SECURED.get(connection)
    if known and time.time() - known[1] < 60:
        return known[0]
    code, out, _ = network._run(["-g", "802-11-wireless-security.key-mgmt", "connection", "show", "id", connection],
                                timeout=5)
    answer = code == 0 and bool(out.strip())
    _SECURED[connection] = (answer, time.time())
    return answer


def mark_reach(found: list[dict], cfg: dict) -> list[dict]:
    """Mark each of the tablet's addresses with whether the settings let a computer see the share there.

    Addresses on other kinds of network (virtual bridges and the like) are dropped.
    """
    keep = []
    for a in found:
        if a["kind"] in ("cable", "direct"):
            a["open"], a["secured"] = bool(cfg.get("nas_cable", True)), True
        elif a["kind"] == "hotspot":
            a["open"], a["secured"] = bool(cfg.get("nas_hotspot", True)), True
        elif a["kind"] == "wifi":
            a["secured"] = _secured(a["network"]) if a["network"] else False
            a["open"] = a["secured"] and a["network"] in (cfg.get("nas_trusted") or [])
        else:
            continue
        keep.append(a)
    found[:] = keep
    return found


def tx_bytes() -> int:
    """Bytes sent by the tablet's network cards so far, to work out how fast the share is sending."""
    total = 0
    for card in Path("/sys/class/net").glob("*"):
        if card.name == "lo":
            continue
        try:
            total += int((card / "statistics" / "tx_bytes").read_text())
        except (OSError, ValueError):
            pass
    return total


def status(cfg: dict) -> dict:
    """Everything the screens show about the NAS."""
    state = system_state()
    out = {
        "installed": installed(),
        "user": state.get("user", ""),
        "library": state.get("library", ""),
        "nas_on": _active("smb.service"),
        "sftp_on": _active("doubletake-sftp.service"),
        "host": host_name(),
        "port": SFTP_PORT,
    }
    out["addresses"] = addresses() if (out["nas_on"] or out["sftp_on"]) else []
    mark_reach(out["addresses"], cfg)
    out["hidden_on"] = [a for a in out["addresses"] if not a["open"]]
    out.update(connections() if (out["nas_on"] or out["sftp_on"]) else {"sessions": [], "open": [], "fresh": False})
    return out

