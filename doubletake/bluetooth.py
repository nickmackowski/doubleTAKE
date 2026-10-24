"""Bluetooth, for a keyboard, a mouse or the like, through BlueZ's bluetoothctl.

Not for the camera: cameras use Bluetooth only for pairing, remote control and small
previews, in a different private protocol for each brand. Photos come over USB.
"""

from __future__ import annotations

import os
import re
import select
import shutil
import subprocess
import time
from pathlib import Path

MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x01|\x02|\r")

# BlueZ's icon names, in words.
KINDS = {
    "input-keyboard": "keyboard", "input-mouse": "mouse", "input-tablet": "pen tablet",
    "input-gaming": "game controller", "audio-headset": "headset", "audio-headphones": "headphones",
    "audio-card": "speaker", "phone": "phone", "computer": "computer", "camera-photo": "camera",
}


def tool() -> str:
    return shutil.which("bluetoothctl") or ""


def _clean(text: str) -> str:
    return _ANSI.sub("", text or "")


def _run(args, timeout: float = 15.0):
    """(exit code, output) of a bluetoothctl command."""
    exe = tool()
    if not exe:
        return 127, ""
    try:
        done = subprocess.run([exe] + list(args), capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
        return done.returncode, _clean(done.stdout + done.stderr)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, _clean(out)
    except OSError:
        return 126, ""


def has_hardware(root: str = "/sys/class/bluetooth") -> bool:
    try:
        return any(Path(root).iterdir())
    except OSError:
        return False


def service_running() -> bool:
    try:
        out = subprocess.run(["systemctl", "is-active", "bluetooth.service"], capture_output=True, text=True,
                             timeout=5).stdout
        return out.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def parse_show(text: str) -> dict:
    """The adapter from 'bluetoothctl show'."""
    if "Controller" not in text:
        return {}
    fields = {}
    for line in text.splitlines():
        if ":" in line and line.startswith(("\t", " ")):
            key, value = line.strip().split(":", 1)
            fields.setdefault(key.strip(), value.strip())
    return {"name": fields.get("Alias") or fields.get("Name", ""), "powered": fields.get("Powered") == "yes",
            "discovering": fields.get("Discovering") == "yes"}


def parse_devices(text: str) -> list:
    """[(address, name)] from 'bluetoothctl devices'."""
    found = []
    for line in text.splitlines():
        match = re.match(r"^\s*Device\s+([0-9A-F:]{17})\s*(.*)$", line.strip())
        if match:
            name = match.group(2).strip()
            found.append((match.group(1), name))
    return found


def parse_info(text: str) -> dict:
    fields = {}
    for line in text.splitlines():
        if ":" in line and line.startswith(("\t", " ")):
            key, value = line.strip().split(":", 1)
            fields.setdefault(key.strip(), value.strip())
    icon = fields.get("Icon", "")
    return {"name": fields.get("Alias") or fields.get("Name", ""), "paired": fields.get("Paired") == "yes",
            "connected": fields.get("Connected") == "yes", "trusted": fields.get("Trusted") == "yes",
            "kind": KINDS.get(icon, icon.split("-")[-1] if icon else ""),
            "rssi": fields.get("RSSI", "").split(" ")[0]}


def _looks_like_address(name: str, address: str) -> bool:
    """BlueZ names a device by its address, with dashes, until it learns its name."""
    return not name or name.replace("-", ":").upper() == address


def status() -> dict:
    """The adapter and its devices: paired ones, and others seen nearby."""
    out = {"tool": bool(tool()), "hardware": has_hardware(), "service": service_running(),
           "powered": False, "name": "", "paired": [], "nearby": []}
    if not (out["tool"] and out["service"]):
        return out
    code, text = _run(["show"], timeout=5)
    adapter = parse_show(text)
    if not adapter:
        return out
    out.update(powered=adapter["powered"], name=adapter["name"])
    code, text = _run(["devices"], timeout=5)
    for address, name in parse_devices(text)[:40]:
        _, detail = _run(["info", address], timeout=5)
        info = parse_info(detail)
        info["address"] = address
        info["name"] = info["name"] or name
        if info["paired"]:
            out["paired"].append(info)
        elif not _looks_like_address(info["name"], address):
            out["nearby"].append(info)  # unnamed devices nearby are not worth showing
    out["paired"].sort(key=lambda d: (not d["connected"], d["name"].lower()))
    out["nearby"].sort(key=lambda d: d["name"].lower())
    return out


def _ok(code: int, text: str) -> bool:
    return code == 0 and not re.search(r"\b(Failed|not available|No default controller)\b", text)


def power(on: bool) -> str:
    """Switch the adapter on or off. Returns an error to show, or ''."""
    code, text = _run(["power", "on" if on else "off"])
    if on and "Blocked" in text and shutil.which("rfkill"):
        subprocess.run(["rfkill", "unblock", "bluetooth"], capture_output=True, timeout=5)
        time.sleep(1)
        code, text = _run(["power", "on"])
    if _ok(code, text):
        return ""
    if "Blocked" in text:
        return "Bluetooth is switched off in hardware or in the system (rfkill). Switch it on there first."
    return "Could not switch Bluetooth " + ("on" if on else "off") + "."


def scan(seconds: int = 12) -> str:
    code, text = _run(["--timeout", str(seconds), "scan", "on"], timeout=seconds + 10)
    if "Failed to start discovery" in text or code not in (0, 124):
        return "Could not look for devices. Check that Bluetooth is on."
    return ""


def remove(address: str) -> str:
    if not MAC_RE.match(address):
        return "Not a Bluetooth address."
    code, text = _run(["remove", address])
    return "" if _ok(code, text) else "Could not remove it."


def connect(address: str, on: bool = True) -> str:
    if not MAC_RE.match(address):
        return "Not a Bluetooth address."
    code, text = _run(["connect" if on else "disconnect", address], timeout=20)
    return "" if _ok(code, text) else ("Could not connect. Check that it is switched on and nearby." if on
                                       else "Could not disconnect.")


def pair(address: str, report, timeout: float = 60.0) -> str:
    """Pair, trust and connect, answering bluetoothctl's questions as it asks them.

    'report(text)' is called with what the person needs to see, such as a passkey to
    type on a keyboard. Returns an error to show, or '' once paired.
    """
    if not MAC_RE.match(address):
        return "Not a Bluetooth address."
    exe = tool()
    if not exe:
        return "Bluetooth is not installed. Run doubletake setup in a terminal."
    proc = subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            bufsize=0)

    def send(line: str) -> None:
        try:
            proc.stdin.write((line + "\n").encode())
            proc.stdin.flush()
        except OSError:
            pass

    buffer, error, paired, connected, paired_at = "", "", False, False, 0.0
    send("agent KeyboardDisplay")
    send("default-agent")
    send(f"pair {address}")
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            if paired and time.time() > paired_at + 10:
                break  # paired; some devices only connect once they are used
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            if not ready:
                continue
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            buffer += _clean(chunk.decode("utf-8", "replace"))
            lines = buffer.split("\n")
            buffer = lines.pop()  # a question waits on an unfinished line
            for line in lines + [buffer]:
                text = line.strip()
                passkey = re.search(r"Passkey:\s*(\d{6})", text)
                confirm = re.search(r"Confirm passkey\s+(\d{6})", text)
                if confirm:
                    report(f"Check that {confirm.group(1)} shows on the device too.")
                    send("yes")
                    buffer = ""
                elif passkey:
                    report(f"Type {passkey.group(1)} on the device, then press Enter.")
                elif re.search(r"Enter PIN code", text):
                    report("Type 0000 on the device, then press Enter, if it asks.")
                    send("0000")
                    buffer = ""
                elif re.search(r"(Authorize service|Request authorization|Accept pairing).*\(yes/no\)", text):
                    send("yes")
                    buffer = ""
                elif "Pairing successful" in text and not paired:
                    paired, paired_at = True, time.time()
                    report("Paired. Connecting...")
                    send(f"trust {address}")
                    send(f"connect {address}")
                elif "Connection successful" in text:
                    connected = True
                elif "Failed to pair" in text or "AuthenticationFailed" in text:
                    error = "Pairing failed. Put the device in pairing mode and try again."
                elif "not available" in text and address in text:
                    error = "The device is no longer nearby. Look for devices again."
            if error or connected:
                break
        else:
            if not paired:
                error = "No answer from the device. Put it in pairing mode and try again."
    finally:
        send("quit")
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    return error
