"""Wi-Fi, the cable, and the tablet's own hotspot, through NetworkManager.

Everything goes through the nmcli command that comes with the desktop. The
person logged in at the tablet's screen may change the network without a
password, so nothing here needs sudo.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess

HOTSPOT = "DoubleTake-Hotspot"  # the name of the hotspot's connection in NetworkManager


def nmcli_path():
    return shutil.which("nmcli")


def _run(args, timeout: float = 15.0):
    """(exit code, output, error text) of an nmcli command."""
    nmcli = nmcli_path()
    if not nmcli:
        return 127, "", "NetworkManager's nmcli was not found."
    try:
        done = subprocess.run([nmcli] + list(args), capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
        return done.returncode, done.stdout, done.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "NetworkManager took too long to answer."
    except OSError as exc:
        return 126, "", str(exc)


def split(line: str) -> list[str]:
    """One line of nmcli's terse output. Fields are split on ':', and '\\:' is a colon in a field."""
    fields, current, escaped = [], [], False
    for ch in line:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


def _rows(output: str) -> list[list[str]]:
    return [split(line) for line in output.splitlines() if line.strip()]


def devices() -> list[dict]:
    code, out, _ = _run(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"], timeout=5)
    if code != 0:
        return []
    found = []
    for row in _rows(out):
        if len(row) >= 4:
            found.append({"device": row[0], "type": row[1], "state": row[2], "connection": row[3]})
    return found


def _wifi_device(found=None):
    for d in found if found is not None else devices():
        if d["type"] == "wifi":
            return d
    return None


def parse_networks(output: str) -> list[dict]:
    """Networks from 'device wifi list': the strongest of each name, the one in use first."""
    best: dict = {}
    for row in _rows(output):
        if len(row) < 4:
            continue
        in_use, ssid, signal, security = row[0], row[1], row[2], row[3]
        if not ssid:
            continue  # a hidden network
        try:
            strength = int(signal)
        except ValueError:
            strength = 0
        item = {"ssid": ssid, "signal": strength, "secure": security not in ("", "--"),
                "in_use": in_use.strip() == "*"}
        old = best.get(ssid)
        if old is None or item["in_use"] or (not old["in_use"] and strength > old["signal"]):
            best[ssid] = item
    return sorted(best.values(), key=lambda n: (not n["in_use"], -n["signal"], n["ssid"].lower()))


def status() -> dict:
    """What the tablet is connected to now."""
    if not nmcli_path():
        return {"available": False}
    found = devices()
    wifi = _wifi_device(found)
    wired = [d for d in found if d["type"] == "ethernet"]
    out = {
        "available": True,
        "wifi_device": wifi["device"] if wifi else "",
        "wifi_state": wifi["state"] if wifi else "missing",
        "ssid": "",
        "signal": 0,
        "hotspot": bool(wifi and wifi["connection"] == HOTSPOT and wifi["state"].startswith("connected")),
        "wired": any(d["state"].startswith("connected") and d["connection"] != "DoubleTake-Direct" for d in wired),
        "direct": any(d["state"].startswith("connected") and d["connection"] == "DoubleTake-Direct" for d in wired),
        "cable_waiting": any(d["state"].startswith("connecting") for d in wired),
    }
    if wifi and wifi["state"].startswith("connected") and not out["hotspot"]:
        code, text, _ = _run(["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                              "ifname", wifi["device"], "--rescan", "no"], timeout=5)
        for net in parse_networks(text) if code == 0 else []:
            if net["in_use"]:
                out["ssid"], out["signal"] = net["ssid"], net["signal"]
                break
        if not out["ssid"]:
            out["ssid"] = wifi["connection"]
    return out


def saved_networks() -> set:
    code, out, _ = _run(["-t", "-f", "NAME,TYPE", "connection", "show"], timeout=5)
    if code != 0:
        return set()
    return {row[0] for row in _rows(out)
            if len(row) >= 2 and row[1] in ("802-11-wireless", "wifi") and row[0] != HOTSPOT}


def scan() -> dict:
    """Look for Wi-Fi networks nearby. Takes a few seconds."""
    wifi = _wifi_device()
    if wifi is None:
        return {"error": "This tablet's Wi-Fi was not found.", "networks": []}
    code, out, err = _run(["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                           "ifname", wifi["device"], "--rescan", "yes"], timeout=30)
    if code != 0:
        return {"error": err or "Could not look for networks.", "networks": []}
    saved = saved_networks()
    networks = parse_networks(out)
    for net in networks:
        net["saved"] = net["ssid"] in saved
    return {"error": "", "networks": networks}


def connect(ssid: str, password: str = "") -> str:
    """Join a network. Returns an error message, or ''."""
    wifi = _wifi_device()
    if wifi is None:
        return "This tablet's Wi-Fi was not found."
    if wifi["connection"] == HOTSPOT:
        _run(["connection", "down", "id", HOTSPOT])
        _forget_hotspot()
    if not password and ssid in saved_networks():
        code, _, err = _run(["connection", "up", "id", ssid], timeout=60)
    else:
        args = ["device", "wifi", "connect", ssid, "ifname", wifi["device"]]
        if password:
            args += ["password", password]
        code, _, err = _run(args, timeout=60)
    if code == 0:
        return ""
    if "Secrets were required" in err or "password" in err.lower():
        return "The password was not accepted."
    return err or "Could not join the network."


def disconnect() -> str:
    wifi = _wifi_device()
    if wifi is None:
        return "This tablet's Wi-Fi was not found."
    code, _, err = _run(["device", "disconnect", wifi["device"]])
    return "" if code == 0 else (err or "Could not disconnect.")


def can_host(device: str) -> bool:
    code, out, _ = _run(["-t", "-f", "WIFI-PROPERTIES.AP", "device", "show", device], timeout=5)
    return code == 0 and out.strip().lower().endswith(":yes")


def hotspot_on(name: str, password: str) -> str:
    """Start the tablet's own network, for a laptop to join. Returns an error message, or ''."""
    wifi = _wifi_device()
    if wifi is None:
        return "This tablet's Wi-Fi was not found."
    if not can_host(wifi["device"]):
        return "This tablet's Wi-Fi cannot run a hotspot."
    _forget_hotspot()  # start from a fresh one each time, so there is never more than one
    code, _, err = _run(["device", "wifi", "hotspot", "ifname", wifi["device"], "con-name", HOTSPOT,
                         "ssid", name, "password", password], timeout=45)
    return "" if code == 0 else (err or "Could not start the hotspot.")


def _forget_hotspot() -> None:
    """Remove the hotspot's settings from NetworkManager. Otherwise NetworkManager can
    pick the hotspot again as the best network for the Wi-Fi, and switch it straight back on."""
    for _ in range(5):  # in case more than one was left behind
        code, _, _ = _run(["connection", "delete", "id", HOTSPOT], timeout=20)
        if code != 0:
            break


def hotspot_off() -> str:
    _run(["connection", "down", "id", HOTSPOT], timeout=20)
    _forget_hotspot()
    wifi = _wifi_device()
    if wifi is not None and wifi["connection"] == HOTSPOT and wifi["state"].startswith("connected"):
        return "The hotspot did not stop. Switch it off in the desktop's Wi-Fi settings."
    rejoin()
    return ""


def rejoin() -> str:
    """Back onto the strongest network in range that the tablet already knows, if there is one."""
    wifi = _wifi_device()
    if wifi is None:
        return ""
    code, out, _ = _run(["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                         "ifname", wifi["device"], "--rescan", "yes"], timeout=30)
    saved = saved_networks()
    for net in parse_networks(out) if code == 0 else []:
        if net["ssid"] in saved:
            code, _, _ = _run(["connection", "up", "id", net["ssid"]], timeout=60)
            return net["ssid"] if code == 0 else ""
    return ""  # none in range: NetworkManager joins one by itself when it comes in range


def new_password() -> str:
    """Ten letters and digits, leaving out ones that look alike (0 and O, 1 and l)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))
