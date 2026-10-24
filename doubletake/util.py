"""Small formatting and naming helpers shared across the engine."""

from __future__ import annotations

import re
import time

STOPPED = "Stopped by you."


class StopRequested(Exception):
    """Raised from a progress callback to stop a job cleanly. The job can be resumed."""


def human_bytes(n: float) -> str:
    """1536 -> '1.5 KB'. Decimal units, to match how drives are sold."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def mb_per_s(nbytes: float, seconds: float) -> float:
    if seconds <= 0:
        return 0.0
    return nbytes / 1_000_000 / seconds


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def local_date(ts: float) -> str:
    """Unix time -> 'YYYY-MM-DD' in the tablet's local time zone."""
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def iso_time(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str) -> str:
    """Turn a typed shoot name into something safe for a folder name.

    'KCLT ramp / evening' -> 'KCLT-ramp-evening'
    """
    cleaned = _UNSAFE.sub("-", name.strip()).strip("-.")
    return cleaned[:60] or "shoot"


# ----- the clock, as the desktop shows it ------------------------------------------------

_CLOCK = {"at": 0.0, "format": "24h"}


def clock_format(now=None, read=None) -> str:
    """"12h" or "24h": the desktop's own clock setting (chosen when the system was set up),
    looked up at most once a minute. 24-hour when it cannot be read."""
    import subprocess

    now = time.time() if now is None else now
    if now - _CLOCK["at"] < 60:
        return _CLOCK["format"]

    def default():
        done = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "clock-format"],
                              capture_output=True, text=True, timeout=5)
        return done.stdout if done.returncode == 0 else ""

    try:
        answer = (read or default)()
    except (OSError, subprocess.SubprocessError):
        answer = ""
    _CLOCK.update(at=now, format="12h" if "12h" in answer else "24h")
    return _CLOCK["format"]


def clock_text(ts=None, fmt=None) -> str:
    """A time of day for the screens, 9:41 PM or 21:41 as the desktop shows it."""
    t = time.localtime(ts)
    if (fmt or clock_format()) == "12h":
        hour = t.tm_hour % 12 or 12
        return f"{hour}:{t.tm_min:02d} {'AM' if t.tm_hour < 12 else 'PM'}"
    return time.strftime("%H:%M", t)


# ----- the version, as people read it -----------------------------------------------------


def version_text(version: str) -> str:
    """'1.0~pr1' -> '1.0 pre-release 1'. The tilde is the package manager's way of saying
    'before 1.0'; people read the words."""
    import re

    m = re.fullmatch(r"(.+?)~pr(\d+)", version or "")
    return f"{m.group(1)} pre-release {m.group(2)}" if m else (version or "")
