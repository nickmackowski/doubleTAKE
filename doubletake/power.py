"""Battery rules, for the tablet and the camera.

- At 30% or below a battery shows amber in the top bar, and at 20% red.
- A backup does not start with the camera at 10% or below, or with the tablet at
  10% or below and not on its charger.
- A backup that is running stops cleanly, after the photo it is on, when either
  reaches that point. The next backup carries on where it stopped.
"""

from __future__ import annotations

import re

STOP = 10


def percent(value) -> int | None:
    """80, '80%', ' 80 % ' -> 80. None when it is not known."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"(\d{1,3})", str(value))
    return int(match.group(1)) if match else None


def camera_problem(level) -> str:
    level = percent(level)
    if level is not None and level <= STOP:
        return (f"The camera battery is at {level}%. Charge it or swap in a fresh one, "
                "then tap Start to carry on.")
    return ""


def tablet_problem(level, on_power: bool) -> str:
    level = percent(level)
    if level is not None and level <= STOP and not on_power:
        return f"The tablet's battery is at {level}%. Plug it in to start a backup."
    return ""


def problem(camera_level, tablet_level, tablet_on_power: bool) -> str:
    """Why a backup may not run now, or ''."""
    return camera_problem(camera_level) or tablet_problem(tablet_level, tablet_on_power)
