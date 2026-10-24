"""The engine as a long-running service, for the screens.

One Station owns the camera, the card and the jobs. The screens only read its
state and press its buttons, so closing or reloading a screen changes nothing.

The copy off the camera comes first. The second copy to the card follows by
itself, in the background, from the tablet's own drive.
"""

from __future__ import annotations

import copy
import getpass
import shutil
import threading
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from . import __version__, awake, bluetooth, cards, config, device, direct, editor, firstrun, kiosk, naming, screen, nas, network, phone, photos, power, poweron, retention, settings, usb
from .ingest import BackupError, Reporter, copy_choice, coverage, make_plan, next_shoot_folder, run_backup, shoot_slug
from .ledger import Ledger
from .mirror import run_mirror
from .sources import SourceError, open_source, source_present
from .util import STOPPED, StopRequested, clock_text, local_date, version_text

# During a backup, the second copy starts once this share of Copy 1 is done.
COPY2_AFTER = 0.10

TICK_SECONDS = 2.0
CAMERA_BATTERY_EVERY = 60.0  # seconds between looks at an idle camera's battery
AUTO_COUNTDOWN = 10.0        # seconds to cancel an unattended backup before it starts
AUTO_DARK_AFTER = 60.0       # seconds the result of an unattended backup stays on the screen
SPLASH_THEME = Path("/usr/share/plymouth/themes/doubletake/doubletake.plymouth")


def _new_job() -> dict:
    return {
        "total_files": 0, "total_bytes": 0, "done_files": 0, "done_bytes": 0.0,
        "verified": 0, "failed": 0, "current": "", "started": 0.0, "finished_at": 0.0,
        "speed_now": 0.0, "failures": [],
    }


class _Progress(Reporter):
    """Feeds one job's progress into the shared state, and stops the job when asked."""

    POWER_EVERY = 20.0  # seconds between looks at the batteries during a copy

    def __init__(self, station: "Station", job: dict, stop: threading.Event, source=None):
        self.station, self.job, self.stop, self.source = station, job, stop, source
        self.mark_time, self.mark_bytes = time.time(), 0.0
        self.power_checked = time.time()

    def _check_power(self) -> None:
        """Every 20 seconds, between photos: stop cleanly if a battery is about to run out."""
        if self.source is None or time.time() - self.power_checked < self.POWER_EVERY:
            return
        self.power_checked = time.time()
        level = None
        try:
            level = self.source.battery_now()
        except Exception:
            pass
        tablet = _tablet_battery()
        with self.station.lock:
            if level is not None:
                self.station.camera["battery"] = f"{level}%"
            if tablet:
                self.station.tablet.update(tablet)
        problem = power.camera_problem(level) or power.tablet_problem(tablet.get("battery"), bool(tablet.get("on_power")))
        if problem:
            with self.station.lock:
                self.job["stop_reason"] = "Stopped to save the battery. " + problem
            raise StopRequested()

    def plan(self, plan, shoot_folder, method):
        with self.station.lock:
            self.job.update(total_files=len(plan.todo), total_bytes=plan.total_bytes, folder=shoot_folder,
                            method=method, started=time.time(), phase="copying")
        self.mark_time = time.time()

    def mirror_start(self, count, nbytes, card):
        with self.station.lock:
            self.job.update(_new_job(), total_files=count, total_bytes=nbytes, started=time.time())
        self.mark_time, self.mark_bytes = time.time(), 0.0

    def file_start(self, index, f):
        with self.station.lock:
            self.job["current"] = f.name

    def bytes(self, n):
        if self.stop.is_set():
            raise StopRequested()
        now = time.time()
        with self.station.lock:
            job = self.job
            job["done_bytes"] += n
            gap = now - self.mark_time
            if gap >= 1.0:
                rate = (job["done_bytes"] - self.mark_bytes) / gap
                job["speed_now"] = rate if not job["speed_now"] else 0.5 * job["speed_now"] + 0.5 * rate
                self.mark_time, self.mark_bytes = now, job["done_bytes"]

    def file_done(self, f, status, note):
        with self.station.lock:
            job = self.job
            job["done_files"] += 1
            if status == "verified":
                job["verified"] += 1
            else:
                job["failed"] += 1
                if len(job["failures"]) < 20:
                    job["failures"].append({"name": f.name, "note": note})
        self._check_power()


class Station:
    def __init__(self, cfg: dict, source: str = "camera", limit: int | None = None):
        self.cfg = cfg
        self.source = source
        self.limit = limit  # for testing: copy at most this many photos per backup
        self.library = Path(cfg["library"])
        self.library.mkdir(parents=True, exist_ok=True)
        self.data_dir = config.data_dir(cfg)
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.quit = threading.Event()
        self.stop_backup = threading.Event()
        self.stop_mirror = threading.Event()

        self.camera = self._no_camera()
        self.plan = None  # what was on the camera's cards when they were last read
        self.plan_time = 0.0
        self.backup = None
        self.mirror = dict(_new_job(), state="idle", message="", write_speed=0.0, read_speed=0.0)
        self.mirror_paused = False  # by the user
        self.mirror_blocked = False  # after an error, until the card is put back or the user retries
        self.card = {"state": "none"}
        self.format = {"known": False}
        self.shoots = []
        self.shoot_count = 0
        self.names = []
        self.tablet = {}
        self.problem = ""
        self.net = {"available": False}
        self.net_job = {"busy": False, "message": "", "error": ""}
        self.bt_job = {"busy": False, "message": "", "error": ""}
        self.phone_share = None
        self.direct = direct.DirectCable()
        self.direct_state = {"active": False, "device": "", "waiting": False, "error": ""}
        self.usb_job = {"busy": False, "done": 0, "total": 0, "current": "", "error": "", "message": "", "mount": ""}
        self.export_job = {"busy": False, "done": 0, "total": 0, "current": "", "error": "", "message": "", "shoot": ""}
        self.nas = {"installed": False}
        self.nas_job = {"busy": False, "message": "", "error": ""}
        self.splash_job = {"busy": False, "error": ""}
        self.status_open = False  # P3's status panel
        self.level_shown = {"id": 0}  # a brightness key pressed: the level, for the screens to show
        self.leaving = None  # on the way out: the switching screen's lines
        self.sound = {"id": 0, "kind": ""}  # the beeps: one at a backup's start, two when done, three low for a problem
        self._ticks = 0
        self._battery_at = time.time()
        self.auto = None           # an unattended backup counting down, or why one did not start
        self._auto_armed = False   # the camera was just plugged in
        self._touch_at = time.time()  # the last touch on the screen, or the camera plugged in
        self.screen_dark = False
        self.awake = awake.StayAwake()
        self._tx = (0.0, 0)  # (time, bytes sent) at the last look, for the NAS's sending speed

        self._holding = False  # the second copy is held off while shoots are being cleared
        self._was_present = False
        self._card_was_ready = False
        self._camera_thread = None
        self._mirror_thread = None
        self._watch_thread = None

    @staticmethod
    def _no_camera() -> dict:
        return {"state": "absent", "message": "", "listed": 0, "model": "", "serial": "", "battery": "",
                "storage": [], "found": 0, "duplicates": 0, "photos": 0, "already": 0, "new": 0,
                "new_bytes": 0, "date": "", "connected": False}

    # ----- starting and stopping -------------------------------------------

    def start(self) -> None:
        try:
            poweron.refresh(self.cfg, getpass.getuser())
        except Exception:
            pass  # the desktop settings are a convenience; the engine runs without them
        try:
            firstrun.user_setup()
        except Exception:
            pass  # tidying after an earlier install; tried again at the next start
        self._watch_thread = threading.Thread(target=self._watch, name="watch", daemon=True)
        self._watch_thread.start()
        if nas.installed() and getattr(self, "keys", None) is None:
            from . import keys

            self.keys = keys.KeyWatcher(self.on_key)
            self.keys.start()
        # Keep the boot menu's maintenance entry on the kernel the tablet now starts.
        if nas.installed():
            def boot_jobs():
                nas.helper("boot-entry", "refresh", timeout=120)
                nas.helper("splash", "tune", timeout=1200)  # the shutdown fade, and the line for this tablet's name
            threading.Thread(target=boot_jobs, name="boot-jobs", daemon=True).start()

    def shutdown(self) -> None:
        """Stop every job cleanly. Anything part-way is picked up again next time."""
        self.quit.set()
        if getattr(self, "keys", None) is not None:
            self.keys.stop()
            self.keys = None
        self.awake.hold(False)
        self.phone_stop()
        self.stop_backup.set()
        self.stop_mirror.set()
        self.wake.set()
        for thread in (self._camera_thread, self._mirror_thread, self._watch_thread):
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=30)

    # ----- the DoubleTake password ------------------------------------------------

    def password_status(self) -> dict:
        if not nas.installed():
            return {"mode": "unset", "wait": 0, "available": False}
        answer = nas.helper("fs-password", "status", timeout=30)
        if not answer.get("ok"):
            return {"mode": "unknown", "wait": 0, "available": True, "error": answer.get("error", "")}
        return {"mode": answer.get("mode", "unset"), "wait": int(answer.get("wait", 0)), "available": True}

    def check_password(self, password: str) -> str:
        """'' when the DoubleTake password lets this through (or none is set), else why not."""
        status = self.password_status()
        if status["mode"] in ("unset", "none"):
            return ""
        if status["mode"] == "unknown":
            return "The DoubleTake password could not be checked: " + status.get("error", "")
        if not password:
            return "Enter the DoubleTake password."
        answer = nas.helper("fs-password", "check", stdin=password, timeout=60)
        if answer.get("ok"):
            return ""
        wait = int(answer.get("wait", 0) or 0)
        return f"Wrong password. Wait {wait} seconds before trying again." if wait else "Wrong password."

    def set_password(self, current: str, new: str, none: bool = False) -> str:
        if not nas.installed():
            return "The DoubleTake password needs the system setup: run doubletake setup in a terminal."
        text = (current + "\n" if current else "") + ("" if none else new)
        answer = nas.helper("fs-password", "none" if none else "set", stdin=text, timeout=60)
        if answer.get("ok"):
            return ""
        error = answer.get("error", "Not changed.")
        return error[0].upper() + error[1:] + ("." if not error.endswith(".") else "")

    def switch_splash(self, on: bool) -> str:
        """Change the boot screen in the background; it takes a minute or two."""
        with self.lock:
            if self.splash_job["busy"]:
                return "The boot screen is being changed already."
            self.splash_job = {"busy": True, "error": ""}

        def run():
            answer = nas.helper("splash", "on" if on else "off", timeout=900)
            with self.lock:
                self.splash_job = {"busy": False, "error": "" if answer.get("ok") else answer.get("error", "")}

        threading.Thread(target=run, name="splash", daemon=True).start()
        return ""

    # ----- unattended backup -------------------------------------------------------

    def _maybe_arm_auto(self, plan) -> None:
        if not (self._auto_armed and self.cfg.get("auto_backup")):
            return
        self._auto_armed = False
        if not plan.todo:
            return
        date = local_date(time.time())
        with self.lock:
            self.auto = {"state": "countdown", "at": time.time() + AUTO_COUNTDOWN, "name": "",
                         "date": date, "message": ""}
        try:
            folder = self.folder_preview("")
        except Exception:
            folder = ""
        with self.lock:
            if self.auto:
                self.auto["folder"] = folder

    def cancel_auto(self) -> None:
        with self.lock:
            self.auto = None

    def _auto_tick(self) -> None:
        auto = self.auto
        if auto and auto.get("state") == "countdown" and time.time() >= auto["at"]:
            error = self.start_backup(auto["name"])
            with self.lock:
                if error:
                    self.auto = {"state": "blocked", "message": error, "name": auto["name"]}
                else:
                    self.auto = None
                    if self.backup is not None:
                        self.backup["auto"] = True
        # After an unattended backup, the result stays up for a minute after the last touch,
        # then the screen goes dark until the camera comes again or the screen is touched.
        b = self.backup
        if (b and b.get("auto") and not b.get("running") and self.cfg.get("auto_dark", True)
                and not self.screen_dark):
            quiet_since = max(float(b.get("finished_at") or 0), self._touch_at)
            if quiet_since and time.time() - quiet_since >= AUTO_DARK_AFTER:
                self.screen_dark = screen.set_power(False)

    def _idle(self, busy: bool) -> None:
        """With no touch for a while: the screen goes dark, and on battery the tablet sleeps.

        Never while photos are on the move. A touch, or the camera, lights it again.
        """
        quiet = time.time() - self._touch_at
        b = self.backup
        dark_after = int(self.cfg.get("screen_dark_after", 5) or 0) * 60
        if dark_after and quiet >= dark_after and not self.screen_dark and not (b and b.get("running")):
            self.screen_dark = screen.set_power(False)
        sleep_after = int(self.cfg.get("sleep_on_battery", 30) or 0) * 60
        on_power = bool((self.tablet or {}).get("on_power", True))
        if sleep_after and quiet >= sleep_after and not busy and not on_power and self.tablet:
            self._touch_at = time.time()  # after waking, the count starts again
            if not screen.suspend():
                print("doubletake: the tablet would not go to sleep (systemctl suspend refused)",
                      file=sys.stderr, flush=True)

    # ----- the tablet's own keys (keys.py) -----------------------------------------

    def on_key(self, key: str) -> None:
        if key.startswith("brightness"):
            return self.brightness_key(key)
        {"p1": self.one_touch_backup, "p2": self.toggle_night_red, "p3": self.toggle_status}[key]()

    def brightness_key(self, key: str) -> None:
        """The screen's brightness keys, in DoubleTake only (on the desktop, the desktop's own
        handler has them). The screens show the level for a moment."""
        if not kiosk.in_kiosk():
            return
        from . import media

        self.wake_screen()
        got = media.press(key)
        if got.get("level") is not None:
            with self.lock:
                self.level_shown = {"id": int((self.level_shown or {}).get("id", 0)) + 1,
                                    "kind": got["kind"], "level": got["level"], "muted": bool(got.get("muted"))}

    def one_touch_backup(self) -> str:
        """P1: back up the new photos now, as Back up on the screens would. One beep as it
        starts, two when it is done; three low ones when it cannot start. Nothing new: two
        beeps straight away. A backup already going: nothing, so a second press is harmless.
        Returns why not, or ''."""
        self.wake_screen()
        with self.lock:
            running = bool(self.backup and self.backup.get("running"))
            state = self.camera.get("state")
            new = int(self.camera.get("new") or 0)
        if running:
            return "A backup is already running."
        if state == "ready" and not new:
            self.beep("done")
            return "Nothing new on the camera."
        if state != "ready":
            self.beep("problem")
            return "No camera with new photos." if state in ("absent", "error") else "The camera is still being read."
        with self.lock:
            self.auto = None  # a countdown, if one was going, is this backup
            self.status_open = False
        error = self.start_backup("")
        if error:
            self.beep("problem")
            return error
        with self.lock:
            if self.backup is not None:
                self.backup["key"] = True
        return ""

    def toggle_status(self) -> None:
        """P3: the status panel, open or closed."""
        with self.lock:
            self.status_open = not self.status_open
        self.wake_screen()

    def toggle_night_red(self) -> None:
        """P2: night red on, or back to the theme before, in the look in use."""
        look, theme = settings.screen_theme(self.cfg)
        red = settings.NIGHT_RED[look]
        key = "cockpit_theme" if look == "cockpit" else "theme"
        if theme == red:
            back = self.cfg.get(f"night_red_from_{look}") or ("cockpit" if look == "cockpit" else "daylight")
            settings.apply(self.cfg, {key: back if back != red else ("cockpit" if look == "cockpit" else "daylight")})
        else:
            settings.apply(self.cfg, {key: red, f"night_red_from_{look}": theme})
        self.wake_screen()

    def wake_screen(self) -> None:
        """Light the screen again (the camera was plugged in, or the screen was touched)."""
        self._touch_at = time.time()
        if self.screen_dark:
            screen.set_power(True)
            self.screen_dark = False

    def _idle_camera_battery(self, present: bool) -> None:
        """About once a minute, while the camera sits plugged in and idle, read its battery again."""
        if not present or time.time() - self._battery_at < CAMERA_BATTERY_EVERY:
            return
        with self.lock:
            if self._alive(self._camera_thread) or self.camera.get("state") not in ("ready",):
                return
            self._battery_at = time.time()

            def run():
                try:
                    with open_source(self.source) as source:
                        level = source.battery_now()
                except Exception:
                    return  # unplugged meanwhile, or busy: the next look tries again
                if level is not None:
                    with self.lock:
                        if self.camera.get("state") == "ready":
                            self.camera["battery"] = f"{level}%"

            self._camera_thread = threading.Thread(target=run, name="camera-battery", daemon=True)
            self._camera_thread.start()

    def leave(self, action: str, background: bool = False, on_gone=None) -> str:
        """Restart or shut down the tablet, or log out to the desktop. Returns why not, or ''.

        Never during a backup from the camera. The second copy is stopped at a safe
        point first, and carries on after the next start.
        """
        if action not in ("restart", "shutdown", "desktop", "logout"):
            return "Unknown action."
        if self._alive(self._camera_thread):
            return "A backup from the camera is running. Stop it, or wait for it to finish, first."
        with self.lock:
            if self.leaving and not self.leaving.get("done"):
                return "The tablet is already on its way."
            self.leaving = {"action": action, "steps": [], "done": False, "error": "", "note": ""}
        if background:
            threading.Thread(target=self._leave_steps, args=(action, on_gone), name="leave", daemon=True).start()
            return ""
        return self._leave_steps(action, on_gone)

    STEP_SECONDS = 0.6   # each line stays at least this long, to be read
    READ_SECONDS = 3.0   # before the last step, with every line done
    LEAVE_NOTE = {
        "desktop": "The screen goes black for about 20 seconds while the desktop starts. This is normal.",
        "logout": "The screen goes black for a few seconds while the tablet logs in again. This is normal.",
        "restart": "The screen goes black while the tablet restarts, then the boot screen. This is normal.",
        "shutdown": "The screen goes black as the tablet shuts down. It is off when the power light is.",
    }
    LAST_LINE = {"desktop": "Starting the desktop", "logout": "Logging in again",
                 "restart": "Restarting the tablet", "shutdown": "Powering off"}

    def _leave_steps(self, action: str, on_gone=None) -> str:
        """The way out, one real step at a time, each listed on the screens as it finishes
        (the switching screen: a line per step, with OK)."""
        state = self.leaving

        def step(doing: str, done: str, work=None) -> str:
            line = {"text": doing, "state": "run"}
            with self.lock:
                state["steps"].append(line)
            started = time.monotonic()
            try:
                problem = (work() if work else "") or ""
            except Exception as exc:
                problem = str(exc) or type(exc).__name__
            # A step too quick to read still gets a moment on the screen.
            time.sleep(max(0.0, self.STEP_SECONDS - (time.monotonic() - started)))
            with self.lock:
                line.update(text=done if not problem else doing, state="fail" if problem else "ok")
            return problem

        def stop_mirror():
            self.stop_mirror.set()
            thread = self._mirror_thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=60)

        def stop_keys():
            if getattr(self, "keys", None) is not None:
                self.keys.stop()

        def close_records():
            self.quit.set()
            self.wake.set()
            thread = self._watch_thread
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=30)

        maint = None
        problem = ""
        mirror_was = self._alive(self._mirror_thread)
        problem = step("Stopping Copy 2 at a safe point" if mirror_was else "Checking Copy 2",
                       "Stopped Copy 2 at a safe point" if mirror_was else "Copy 2 is at rest", stop_mirror)
        if not problem and self.phone_share is not None:
            problem = step("Closing the phone share", "Closed the phone share", self.phone_stop)
        if not problem:
            problem = step("Releasing the tablet keys", "Released the tablet keys", stop_keys)
        if not problem:
            problem = step("Saving the records", "Saved the records", close_records)
        if not problem and action == "desktop":
            from . import maintenance as maint

            def ask():
                settings.set_autostart(settings.AUTOSTART.is_file())  # an older entry gets --autostart
                return maint.ask()

            problem = (step("Asking for one Maintenance visit", "Asked for one Maintenance visit", ask)
                       or step("Switching the next login to the desktop", "Switched the next login to the desktop",
                               maint.switch_login))
        if not problem:
            self.shutdown()
            # Everything done but the last step: a moment to read the list, and what comes next.
            with self.lock:
                state["note"] = self.LEAVE_NOTE[action]
            time.sleep(self.READ_SECONDS)
            out = {"desktop": ("Logging out of DoubleTake", "Logged out of DoubleTake"),
                   "logout": ("Logging out of DoubleTake", "Logged out of DoubleTake"),
                   "restart": ("Closing DoubleTake", "Closed DoubleTake"),
                   "shutdown": ("Closing DoubleTake", "Closed DoubleTake")}[action]
            problem = step(*out, (lambda: kiosk.log_out()) if action in ("desktop", "logout")
                           else (lambda: kiosk.power(action)))
        if problem:
            if maint is not None:
                maint.finish()
            # Still here: carry on as before.
            self.quit.clear()
            self.stop_backup.clear()
            self.stop_mirror.clear()
            self.start()
            with self.lock:
                state.update(done=True, error=problem)
            return problem
        with self.lock:
            state["steps"].append({"text": self.LAST_LINE[action], "state": "wait"})
            state["done"] = True
        if on_gone is not None:
            time.sleep(2.0)  # the screens read the last lines first
            on_gone()
        return ""

    def _alive(self, thread) -> bool:
        return thread is not None and thread.is_alive()

    # ----- the watcher -----------------------------------------------------------

    def _watch(self) -> None:
        # The records may be busy for a moment (DoubleTake carrying on after a way out that
        # failed): try again rather than leave the screens with no watcher.
        ledger = None
        while ledger is None and not self.quit.is_set():
            try:
                ledger = Ledger(self.library, self.data_dir)
            except Exception as exc:
                self.problem = f"{type(exc).__name__}: {exc}"
                self.quit.wait(1.0)
        if ledger is None:
            return
        try:
            while not self.quit.is_set():
                try:
                    self._tick(ledger)
                    self.problem = ""
                except Exception as exc:  # the watcher must survive anything
                    self.problem = f"{type(exc).__name__}: {exc}"
                self.wake.wait(TICK_SECONDS)
                self.wake.clear()
        finally:
            ledger.close()

    def _tick(self, ledger: Ledger) -> None:
        # With no desktop to open cards and drives (DoubleTake only), open them here.
        if self._ticks % 3 == 0 and kiosk.in_kiosk():
            cards.open_removable()
        # The camera: notice it being plugged in or pulled out.
        present = bool(source_present(self.source))
        if present and not self._was_present:
            self._auto_armed = bool(self.cfg.get("auto_backup"))
            self.wake_screen()
        if not present and self.auto and self.auto.get("state") == "countdown":
            with self.lock:
                self.auto = None  # unplugged before it began
        if not self._alive(self._camera_thread):
            if present and not self._was_present:
                self.scan()
            elif not present:
                with self.lock:
                    if self.camera["state"] != "absent" or self.camera["connected"]:
                        self.camera = self._no_camera()
        with self.lock:
            self.camera["connected"] = present
        self._was_present = present
        self._idle_camera_battery(present)
        self._auto_tick()
        # No sleep while photos are on the move.
        b = self.backup
        busy = (bool(b and b.get("running")) or self._alive(self._mirror_thread)
                or bool(self.usb_job.get("busy")) or bool(self.export_job.get("busy"))
                or bool(self.auto and self.auto.get("state") == "countdown"))
        self.awake.hold(busy)
        self._idle(busy)

        # The card for the second copy.
        found = cards.detect(self.cfg, ledger)
        info = {"state": found.state, "unopened": found.unopened, "wanted": found.wanted,
                "kind": found.kind, "other_kind": found.other_kind,
                "others": [str(c.mount) for c in found.all] if found.state == "several" else []}
        ready = found.state == "ready"
        if found.card is not None:
            c = found.card
            info.update(label=c.label, noun=c.noun, style=c.style, total=c.total, free=c.free, mount=str(c.mount),
                        fstype=c.fstype, folder=str(c.root))
        if ready:
            held, left, left_bytes = ledger.copy_totals(found.card.card_id)
            info.update(held=held, waiting=left, waiting_bytes=left_bytes)
        if not ready and self._card_was_ready:
            self.mirror_blocked = False  # the card was taken out, so putting it back may try again
        self._card_was_ready = ready
        with self.lock:
            self.card = info

        # Photos renamed while this card was out are renamed on it now, before anything else.
        if ready and not self._alive(self._mirror_thread) and not self._holding:
            try:
                naming.apply_pending(ledger, found.card, self.library)
            except Exception:
                pass  # tried again on the next look
        # Shoots cleared while this card was out are removed from it now.
        if ready and not self._alive(self._mirror_thread) and not self._holding:
            if retention.card_cleanup(ledger, found.card):
                held, left, left_bytes = ledger.copy_totals(found.card.card_id)
                info.update(held=held, waiting=left, waiting_bytes=left_bytes)

        # The second copy runs by itself whenever there is something for it to do. During a
        # backup it starts once a tenth of Copy 1 is done, and follows behind it: it only
        # ever takes photos already verified on the tablet.
        held_back = not self._copy1_far_enough()
        if ready and not self._alive(self._mirror_thread) and not self._holding:
            if info["waiting"] and not held_back and not self.mirror_paused and not self.mirror_blocked:
                self.stop_mirror.clear()
                self._mirror_thread = threading.Thread(target=self._run_mirror, name="mirror", daemon=True)
                self._mirror_thread.start()
            elif not info["waiting"]:
                with self.lock:
                    if self.mirror["state"] in ("idle", "paused"):
                        self.mirror.update(state="idle", message="")

        # Shoots, and the names used lately.
        rows = [r for r in ledger.list_shoots() if r["cleared"] is None]
        shoots = [{"id": r["id"], "folder": r["folder"], "name": r["name"], "date": r["date"],
                   "files": r["verified"], "failed": r["failed"], "mirrored": r["mirrored"], "bytes": r["bytes"],
                   "imported": bool(r["imported"]), "cover": ledger.cover(r["id"])} for r in rows[:4]]
        names = ledger.recent_names(4)

        # May the camera's cards be formatted?
        plan = self.plan
        if plan is not None:
            cov = coverage(plan, ledger)
            fmt = {"known": True, "photos": cov.photos, "on_tablet": cov.on_tablet, "on_second": cov.on_second,
                   "cleared": cov.cleared, "safe": cov.safe_to_format, "copied_safe": cov.copied_safe, "skipped": dict(plan.skipped or {}),
                   "copy_what": copy_choice(self.cfg), "read_at": clock_text(self.plan_time),
                   "connected": present}
        else:
            fmt = {"known": False}

        try:
            usage = shutil.disk_usage(self.library)
            free, size = usage.free, usage.total
        except OSError:
            free = size = 0
        tablet = {"free": free, "total": size, "library": str(self.library), "data_dir": str(self.data_dir)}
        tablet.update(_tablet_battery())
        net = network.status()
        self._ticks += 1
        # A cable straight to a laptop: no network answers, so the tablet makes one.
        try:
            zone = "home" if self.cfg.get("nas_cable", True) else "public"
            self.direct_state = self.direct.tick(network.devices(), bool(self.cfg.get("direct_cable", True)), zone)
        except Exception:
            pass
        net["direct_waiting"] = self.direct_state.get("waiting", False)
        # A phone share ends with the hotspot, or when its time is up.
        if self.phone_share is not None and (not net.get("hotspot") or not self.phone_share.running()):
            self.phone_stop()
        # Everything else on the home screen is saved below whatever the NAS says: a NAS that
        # cannot be read must not blank the shoots or the batteries (as in 0.8.1).
        try:
            nas_now = nas.status(self.cfg)
        except Exception as exc:
            self.problem = f"NAS: {type(exc).__name__}: {exc}"
            nas_now = dict(self.nas or {}, installed=False, sessions=[], nas_on=False, sftp_on=False)
        now_tx = (time.time(), nas.tx_bytes())
        if self._tx[0] and nas_now["sessions"]:
            nas_now["speed"] = max(0.0, (now_tx[1] - self._tx[1]) / max(0.5, now_tx[0] - self._tx[0]))
        else:
            nas_now["speed"] = 0.0
        self._tx = now_tx
        # Every half a minute, make sure each known network is in the firewall zone the settings want.
        if nas_now["installed"] and self._ticks % 15 == 1 and not self.nas_job["busy"]:
            problems = nas.sync_zones(self.cfg)
            if self._ticks == 1 and not self.cfg.get("nas_hotspot", True):
                nas.helper("hotspot-zone", "off")  # setup opens the hotspot to the share; the setting says no
            nas_now["zone_problem"] = problems[0] if problems else ""
        # The share is on but nobody has said who is connected for a while: start the status writer.
        if (nas_now["installed"] and (nas_now["nas_on"] or nas_now["sftp_on"]) and not nas_now.get("fresh")
                and self._ticks % 15 == 4 and not self.nas_job["busy"]):
            nas.helper("status-check")
        with self.lock:
            self.shoots, self.shoot_count, self.names = shoots, len(rows), names
            self.format, self.tablet, self.net = fmt, tablet, net
            self.nas = nas_now

    # ----- the camera ------------------------------------------------------------

    def scan(self) -> bool:
        """Read the camera's cards and work out what is new."""
        reader = self._camera_thread
        if reader is not None and reader.name == "camera-battery":
            reader.join(timeout=10)
        with self.lock:
            if self._alive(self._camera_thread):
                return False
            self.camera = dict(self._no_camera(), state="scanning", connected=True)
            self._camera_thread = threading.Thread(target=self._run_scan, name="scan", daemon=True)
            self._camera_thread.start()
        return True

    def _listing(self, count: int) -> None:
        with self.lock:
            self.camera["listed"] = count
            if self.backup is not None and self.backup.get("running"):
                self.backup["listed"] = count

    def _take_plan(self, plan) -> None:
        info = plan.info
        with self.lock:
            self.plan, self.plan_time = plan, time.time()
            self.camera.update(
                state="ready", message="", model=info.model, serial=info.serial, battery=info.battery,
                storage=list(info.storage), found=plan.found, duplicates=plan.duplicates,
                photos=len(plan.photos), already=plan.already, new=len(plan.todo), new_bytes=plan.total_bytes,
                skipped=dict(plan.skipped or {}), copy_what=copy_choice(self.cfg),
                date=local_date(min(f.mtime for f in plan.todo)) if plan.todo else "", connected=True,
            )

    def _run_scan(self) -> None:
        ledger = Ledger(self.library, self.data_dir)
        try:
            with open_source(self.source) as source:
                info = source.info()
                with self.lock:
                    self.camera.update(model=info.model, serial=info.serial, battery=info.battery,
                                       storage=list(info.storage))
                plan = make_plan(source, ledger, on_listing=self._listing, limit=self.limit, copy=copy_choice(self.cfg))
                self._take_plan(plan)
                self._maybe_arm_auto(plan)
        except SourceError as exc:
            with self.lock:
                self.camera.update(state="error", message=str(exc))
        except Exception as exc:
            with self.lock:
                self.camera.update(state="error", message=f"Could not read the camera: {exc}")
        finally:
            ledger.close()
            self.wake.set()

    # ----- the backup ------------------------------------------------------------

    def power_problem(self) -> str:
        """Why a backup may not start now because of a battery, or ''."""
        tablet = self.tablet or {}
        return power.problem(self.camera.get("battery"), tablet.get("battery"), bool(tablet.get("on_power")))

    def start_backup(self, name: str) -> str:
        """Start a backup. Returns why not, or ''."""
        problem = self.power_problem()
        if problem:
            return problem
        reader = self._camera_thread
        if reader is not None and reader.name == "camera-battery":
            reader.join(timeout=10)  # a quick look at the battery: let it finish
        with self.lock:
            if self._alive(self._camera_thread):
                return "The camera is busy."
            name = shoot_slug(name)
            self.stop_backup.clear()
            self.backup = dict(_new_job(), running=True, phase="reading", name=name, folder="", method="",
                               listed=0, interrupted=False, reason="", error="", nothing=False,
                               previews_ok=0, previews_bad=0, manifest="", started=time.time(),
                               card_at_start=self.card.get("state"))
            self._camera_thread = threading.Thread(target=self._run_backup, args=(name,), name="backup",
                                                   daemon=True)
            self._camera_thread.start()
        self.beep("start")
        return ""

    def beep(self, kind: str) -> None:
        """The screens play it: 'start' one beep, 'done' two, 'problem' three low ones."""
        if not self.cfg.get("auto_chime", True):
            return
        with self.lock:
            self.sound = {"id": int(self.sound.get("id", 0)) + 1, "kind": kind}

    def _copy1_far_enough(self) -> bool:
        """May the second copy run while a backup is going? Once COPY2_AFTER of it is done."""
        b = self.backup
        if b is None or not b.get("running"):
            return True
        total, done = b.get("total_bytes") or 0, b.get("done_bytes") or 0
        return b.get("phase") != "reading" and total > 0 and done >= COPY2_AFTER * total

    def _run_backup(self, name: str) -> None:
        job = self.backup
        ledger = Ledger(self.library, self.data_dir)
        result = None
        try:
            with open_source(self.source) as source:
                plan = make_plan(source, ledger, on_listing=self._listing, limit=self.limit, copy=copy_choice(self.cfg))
                self._take_plan(plan)
                if not plan.todo:
                    with self.lock:
                        job["nothing"] = True
                else:
                    result = run_backup(source, self.cfg, ledger, name,
                                        reporter=_Progress(self, job, self.stop_backup, source), plan=plan)
                    try:
                        self._take_plan(make_plan(source, ledger, limit=self.limit, copy=copy_choice(self.cfg)))
                    except SourceError:
                        pass  # the camera has gone; the next plug-in reads it again
        except (BackupError, SourceError) as exc:
            with self.lock:
                job["error"] = str(exc)
        except Exception as exc:
            with self.lock:
                job["error"] = f"The backup stopped unexpectedly: {exc}"
        finally:
            with self.lock:
                if result is not None:
                    job.update(name=result.name or job["name"])  # Continue carries on in the same folder
                    job.update(verified=result.verified, failed=result.failed, done_bytes=result.bytes_done,
                               interrupted=result.interrupted, reason=job.get("stop_reason") or result.reason,
                               folder=result.shoot_folder, manifest=result.manifest,
                               previews_ok=result.previews_ok, previews_bad=result.previews_bad,
                               failures=[{"name": n, "note": note} for n, note in result.failures[:20]])
                job.update(running=False, phase="finished", finished_at=time.time())
                # Two beeps: done. Three low ones: it needs a look. Stopped by hand: none.
                if job.get("error") or job.get("failed") or (job.get("interrupted") and job.get("reason") != STOPPED):
                    self.beep("problem")
                elif not job.get("interrupted"):
                    self.beep("done")
                # New photos are on the tablet, so a second copy that had stopped may go again.
                if result is not None and result.verified:
                    self.mirror_blocked = False
                    threading.Thread(target=self._make_cover, args=(result.shoot_folder,), daemon=True).start()
            ledger.close()
            self.wake.set()

    def _make_cover(self, shoot_folder: str) -> None:
        """Make the shoot's picture for the library straight away, so it is ready when looked at."""
        try:
            rel = str(Path(shoot_folder).relative_to(self.library))
            with self._ledger() as ledger:
                row = ledger.db.execute("SELECT id FROM shoots WHERE folder=?", (rel,)).fetchone()
                cover = ledger.cover(row["id"]) if row else None
            found = self.photo_file(cover) if cover else None
            if found:
                photos.picture(found[0], "thumb", found[1])
        except Exception:
            pass  # only a convenience: the picture is made when it is first asked for

    def stop_backup_now(self) -> None:
        self.stop_backup.set()

    def dismiss(self) -> None:
        """Leave the backup-complete screen."""
        with self.lock:
            if self.backup is not None and not self.backup.get("running"):
                self.backup = None

    def folder_preview(self, name: str) -> str:
        """The folder the next backup with this name goes in: 2026_1015_import_01."""
        with self._ledger() as ledger:
            return next_shoot_folder(self.library, local_date(time.time()), name, ledger)

    # ----- the second copy ---------------------------------------------------------

    def _run_mirror(self) -> None:
        job = self.mirror
        ledger = Ledger(self.library, self.data_dir)
        try:
            while not self.stop_mirror.is_set():
                found = cards.detect(self.cfg, ledger)
                if found.state != "ready" or not ledger.copy_totals(found.card.card_id)[1]:
                    with self.lock:
                        job.update(state="idle", message="", current="")
                    break
                with self.lock:
                    job.update(_new_job(), state="running", message="")
                result = run_mirror(self.cfg, ledger, found.card,
                                    reporter=_Progress(self, job, self.stop_mirror))
                with self.lock:
                    job["finished_at"] = time.time()
                    if result.write_seconds > 0:
                        job["write_speed"] = result.write_bytes / result.write_seconds
                    if result.read_seconds > 0:
                        job["read_speed"] = result.read_bytes / result.read_seconds
                    if result.interrupted and result.reason == STOPPED:
                        job.update(state="paused", message="")
                        break
                    if result.interrupted:
                        job.update(state="error", message=result.reason)
                        self.mirror_blocked = True
                        break
                    if result.failed:
                        first = result.failures[0]
                        job.update(state="error",
                                   message=f"{result.failed} files could not be copied. {first[0]}: {first[1]}")
                        self.mirror_blocked = True
                        break
            else:
                with self.lock:
                    job.update(state="paused", message="")
        except Exception as exc:
            with self.lock:
                job.update(state="error", message=f"The second copy stopped unexpectedly: {exc}")
            self.mirror_blocked = True
        finally:
            ledger.close()
            self.wake.set()

    def pause_mirror(self) -> None:
        self.mirror_paused = True
        self.stop_mirror.set()
        with self.lock:
            if not self._alive(self._mirror_thread):
                self.mirror.update(state="paused", message="")

    def resume_mirror(self) -> None:
        self.mirror_paused = False
        self.mirror_blocked = False
        with self.lock:
            if self.mirror["state"] in ("paused", "error"):
                self.mirror.update(state="idle", message="")
        self.wake.set()

    def card_use(self) -> str:
        """Use the card that is inserted. Returns an error message, or ''."""
        ledger = Ledger(self.library, self.data_dir)
        try:
            found = cards.detect(self.cfg, ledger)
            pool = [found.card] if found.card is not None else found.all
            if len(pool) != 1:
                return "There is no single card to use."
            card = pool[0]
            if card.read_only:
                return "The card is read-only, so it cannot be used."
            cards.allow(card, ledger)
            # A card or drive becomes DoubleTake's only by being prepared (erased) for it; one
            # set aside before is offered again. One already DoubleTake's is used as it is.
            if not card.device.startswith("/dev/") and not card.card_id:
                # A folder set by hand (for tests, or a drive the system mounts itself) is
                # never erased: it is taken as it is.
                try:
                    cards.tag(card, ledger)
                except OSError as exc:
                    return f"The folder cannot be written to ({exc})."
            if card.card_id:
                ledger.set_card(f"id:{card.card_id}", "used", card.label)
            self.mirror_paused = self.mirror_blocked = False
            return ""
        finally:
            ledger.close()
            self.wake.set()

    def card_ignore(self) -> str:
        ledger = Ledger(self.library, self.data_dir)
        try:
            found = cards.detect(self.cfg, ledger)
            pool = [found.card] if found.card is not None else found.all
            if len(pool) != 1:
                return "There is no single card to ignore."
            self.stop_mirror.set()
            cards.decline(pool[0], ledger)
            return ""
        finally:
            ledger.close()
            self.wake.set()

    # ----- the shoot library and the viewer ---------------------------------------

    @contextmanager
    def _ledger(self):
        ledger = Ledger(self.library, self.data_dir)
        try:
            yield ledger
        finally:
            ledger.close()

    def library_view(self) -> dict:
        now = time.time()
        with self._ledger() as ledger:
            rows = ledger.list_shoots()
            shoots = []
            for r in rows:
                from_t = retention.deletable_from(ledger, r["id"]) if not r["cleared"] else 0
                shoots.append({
                    "id": r["id"], "folder": r["folder"], "name": r["name"], "date": r["date"],
                    "files": r["verified"], "failed": r["failed"], "bytes": r["bytes"], "mirrored": r["mirrored"],
                    "imported": time.strftime("%Y-%m-%d", time.localtime(r["imported"])) if r["imported"] else "",
                    "cleared": bool(r["cleared"]),
                    "deletable": not r["cleared"] and now >= from_t,
                    "deletable_from": "" if r["cleared"] or now >= from_t else
                        (("today " if time.localtime(from_t).tm_yday == time.localtime(now).tm_yday else
                          time.strftime("%a ", time.localtime(from_t))) + clock_text(from_t)),
                    "cover": None if r["cleared"] else ledger.cover(r["id"]),
                })
        here = [s for s in shoots if not s["cleared"]]
        with self.lock:
            card = copy.deepcopy(self.card)
            free = self.tablet.get("free", 0)
        return {
            "shoots": shoots, "card": card, "free": free,
            "totals": {"shoots": len(here), "photos": sum(s["files"] for s in here),
                       "bytes": sum(s["bytes"] for s in here)},
            "viewer": photos.have_pillow(),
        }

    def shoot_photos(self, shoot_id: int):
        with self._ledger() as ledger:
            shoot = ledger.shoot(shoot_id)
            if shoot is None:
                return None
            files = [{"id": f["id"], "name": f["rel_name"] or f["src_name"], "taken": f["mtime"], "size": f["size"]}
                     for f in ledger.shoot_files(shoot_id) if f["status"] == "verified"]
        edited = editor.edited_ids(self.data_dir)
        for f in files:
            f["edited"] = f["id"] in edited
            f["exported"] = editor.export_path(self.library, shoot["folder"], f["name"]).is_file()
        return {"id": shoot["id"], "folder": shoot["folder"], "name": shoot["name"],
                "cleared": shoot["cleared"] is not None, "files": [] if shoot["cleared"] else files}

    def photo_file(self, file_id: int):
        """(path to the photo, folder for its kept pictures), or None."""
        with self._ledger() as ledger:
            row = ledger.file_by_id(file_id)
        if row is None or row["cleared"] is not None or row["status"] != "verified":
            return None
        path = self.library / row["shoot_folder"] / (row["rel_name"] or row["src_name"])
        return path, self.data_dir / "previews" / str(file_id)

    def photo_info(self, file_id: int):
        found = self.photo_file(file_id)
        if found is None:
            return None
        path = found[0]
        info = photos.read_details(path)
        size = photos.full_size(path)
        info.update(name=path.name, width=size[0] if size else 0, height=size[1] if size else 0)
        return info

    # ----- renaming photos ------------------------------------------------------------

    def rename_preview(self, shoot_id: int, pattern: str = "") -> dict:
        pattern = pattern or self.cfg.get("rename_pattern") or naming.DEFAULT_PATTERN
        with self._ledger() as ledger:
            plan = naming.preview_shoot(ledger, shoot_id, pattern)
        if plan.get("error"):
            return plan
        sample = [{"old": old, "new": new} for _, old, new in plan["moves"][:4]]
        return {"changes": plan["changes"], "total": len(plan["moves"]), "sample": sample, "pattern": pattern}

    def rename_shoot(self, shoot_id: int, pattern: str = "") -> dict:
        pattern = pattern or self.cfg.get("rename_pattern") or naming.DEFAULT_PATTERN
        with self.lock:
            if self.backup is not None and self.backup.get("running"):
                return {"error": "Wait for the backup to finish."}
        with self._mirror_held():
            with self._ledger() as ledger:
                card = self._ready_card(ledger)
                try:
                    done = naming.rename_shoot(ledger, self.library, shoot_id, pattern, card)
                except OSError as exc:
                    return {"error": f"Nothing was renamed: {exc}"}
        self.wake.set()
        return done

    # ----- quick edits and exports -------------------------------------------------

    def _photo_row(self, file_id: int):
        with self._ledger() as ledger:
            row = ledger.file_by_id(file_id)
        if row is None or row["cleared"] is not None or row["status"] != "verified":
            return None
        return row

    def edit_info(self, file_id: int):
        row = self._photo_row(file_id)
        if row is None:
            return None
        name = row["rel_name"] or row["src_name"]
        target = editor.export_path(self.library, row["shoot_folder"], name)
        return {"id": file_id, "name": name, "shoot": Path(row["shoot_folder"]).name,
                "params": editor.load(self.data_dir, file_id), "neutral": editor.neutral(),
                "exported": target.is_file(), "export_name": target.name}

    def edit_save(self, file_id: int, params) -> str:
        if self._photo_row(file_id) is None:
            return "That photo is not on the tablet."
        editor.save(self.data_dir, file_id, params)
        return ""

    def edit_preview(self, file_id: int, params, crop: bool):
        found = self.photo_file(file_id)
        if found is None:
            return None
        path, cache = found
        screen = photos.picture(path, "screen", cache)
        if screen is None:
            return None
        return editor.preview(screen, params, crop=crop)

    def start_export(self, ids: list, size: str, quality: str) -> str:
        if size not in editor.SIZES or quality not in editor.QUALITIES:
            return "Choose a size and a quality."
        rows = [r for r in (self._photo_row(i) for i in ids[:2000]) if r is not None]
        if not rows:
            return "Nothing to export."
        with self.lock:
            if self.export_job["busy"]:
                return "Already exporting. Wait for it to finish."
            self.export_job = {"busy": True, "done": 0, "total": len(rows), "current": "", "error": "",
                               "message": "", "shoot": Path(rows[0]["shoot_folder"]).name}
        threading.Thread(target=self._run_export, args=(rows, size, quality), name="export", daemon=True).start()
        return ""

    def _run_export(self, rows, size: str, quality: str) -> None:
        problems, made = [], 0
        for row in rows:
            name = row["rel_name"] or row["src_name"]
            with self.lock:
                self.export_job["current"] = name
            path = self.library / row["shoot_folder"] / name
            try:
                editor.export(path, editor.load(self.data_dir, row["id"]),
                              editor.export_path(self.library, row["shoot_folder"], name), size, quality)
                made += 1
            except Exception as exc:
                problems.append(f"{name}: {exc}")
            with self.lock:
                self.export_job["done"] += 1
        with self.lock:
            self.export_job.update(busy=False, current="", error="; ".join(problems[:3]),
                                   message=f"Exported {made} photo{'s' if made != 1 else ''}." if made else "")
        self.wake.set()

    def exports(self) -> dict:
        shoots = editor.list_exports(self.library)
        total = sum(f["size"] for s in shoots for f in s["files"])
        return {"shoots": shoots, "count": sum(len(s["files"]) for s in shoots), "bytes": total,
                "folder": str(editor.exports_dir(self.library))}

    def delete_export(self, shoot: str, name: str) -> str:
        names = [name] if name else [f["name"] for s in editor.list_exports(self.library) if s["shoot"] == shoot
                                     for f in s["files"]]
        removed = 0
        for one in names:
            path = editor.export_file(self.library, shoot, one)
            if path is not None:
                path.unlink()
                removed += 1
        folder = editor.exports_dir(self.library) / shoot
        try:
            folder.rmdir()  # only when empty
        except OSError:
            pass
        return "" if removed else "Nothing was removed."

    # ----- exports to a phone, over the hotspot -------------------------------------

    def phone_start(self, shoot: str = "") -> dict:
        """Start a phone share of the exports. Returns the two QR codes, or an error."""
        files = self._export_files(shoot)
        if not files:
            return {"error": "There are no exports to send."}
        if not (self.net or {}).get("hotspot"):
            return {"error": "The hotspot is off.", "need_hotspot": True}
        spot = next((a for a in nas.addresses() if a["kind"] == "hotspot"), None)
        if spot is None:
            return {"error": "The hotspot has no address yet. Try again in a moment.", "need_hotspot": True}
        self.phone_stop()
        answer = nas.helper("phone-port", "on", spot["device"])
        if not answer.get("ok"):
            return {"error": "Could not open the hotspot for the phone: " + answer.get("error", "")}
        device = spot["device"]
        share = phone.PhoneShare(files, spot["address"], on_stop=lambda: nas.helper("phone-port", "off", device))
        try:
            share.start()
        except OSError as exc:
            nas.helper("phone-port", "off", device)
            return {"error": f"Could not start the download page: {exc}"}
        self.phone_share = share
        name, password = self.hotspot_credentials()
        return {"url": share.url, "wifi": name, "count": len(files),
                "qr_wifi": phone.qr_svg(phone.wifi_text(name, password)), "qr_url": phone.qr_svg(share.url),
                "password": password}

    def phone_stop(self) -> None:
        share, self.phone_share = self.phone_share, None
        if share is not None:
            share.stop()

    # ----- exports to a USB stick ---------------------------------------------------

    def usb_drives(self) -> list:
        return usb.drives(self.cfg)

    def _export_files(self, shoot: str = "") -> list:
        out = []
        for s in editor.list_exports(self.library):
            if shoot and s["shoot"] != shoot:
                continue
            for f in s["files"]:
                path = editor.export_file(self.library, s["shoot"], f["name"])
                if path is not None:
                    out.append((s["shoot"], f["name"], str(path)))
        return out

    def usb_copy(self, mount: str, shoot: str = "") -> str:
        drive = next((d for d in self.usb_drives() if d["mount"] == mount), None)
        if drive is None:
            return "That USB stick is not plugged in."
        files = self._export_files(shoot)
        if not files:
            return "There are no exports to copy."
        with self.lock:
            if self.usb_job["busy"]:
                return "Already copying. Wait for it to finish."
            self.usb_job = {"busy": True, "done": 0, "total": len(files), "current": "", "error": "", "message": "",
                            "mount": mount}

        def progress(i, name):
            with self.lock:
                self.usb_job.update(done=i, current=name)

        def run():
            try:
                done = usb.copy(files, mount, progress)
                error = "; ".join(done["problems"][:3])
                message = f"Copied {done['copied']} photo{'s' if done['copied'] != 1 else ''} to {drive['label']}, each checked."
            except OSError as exc:
                error, message = str(exc), ""
            with self.lock:
                self.usb_job.update(busy=False, done=len(files), current="", error=error, message=message)
            self.wake.set()

        threading.Thread(target=run, name="usb", daemon=True).start()
        return ""

    def usb_eject(self, mount: str) -> str:
        drive = next((d for d in self.usb_drives() if d["mount"] == mount), None)
        if drive is None:
            return ""  # already gone
        with self.lock:
            if self.usb_job["busy"]:
                return "Wait for the copy to finish."
        error = usb.eject(mount, drive["device"])
        if not error:
            with self.lock:
                self.usb_job.update(message="Ejected. The USB stick can be pulled out.", error="")
        return error

    # ----- imported, clearing and settings ----------------------------------------

    @contextmanager
    def _mirror_held(self):
        """Keep the second copy still while files are being removed."""
        self._holding = True
        try:
            if self._alive(self._mirror_thread):
                self.stop_mirror.set()
                self._mirror_thread.join(timeout=120)
            yield
        finally:
            self._holding = False
            self.wake.set()

    def _ready_card(self, ledger):
        found = cards.detect(self.cfg, ledger)
        return found.card if found.state == "ready" else None

    def mark_imported(self, shoot_id: int, on: bool) -> dict:
        with self._ledger() as ledger:
            shoot = ledger.shoot(shoot_id)
            if shoot is None or shoot["cleared"] is not None:
                return {"error": "That shoot is no longer on the tablet."}
            ledger.set_imported(shoot_id, time.time() if on else None)
        self.wake.set()
        return {"error": ""}

    def clear(self, shoot_ids) -> dict:
        """Delete shoots from Copy 1 and Copy 2 (see retention)."""
        removed = freed = 0
        errors = []
        b = self.backup
        busy_folder = b.get("folder") if b and b.get("running") else ""
        if busy_folder:
            with self._ledger() as ledger:
                for shoot_id in list(shoot_ids):
                    s = ledger.shoot(int(shoot_id))
                    if s is not None and str(busy_folder).endswith(s["folder"]):
                        return {"error": "That shoot is being backed up right now. Wait for the backup to finish.",
                                "removed": 0, "freed": 0}
        with self._mirror_held():
            with self._ledger() as ledger:
                card = self._ready_card(ledger)
                for shoot_id in shoot_ids:
                    try:
                        done = retention.clear_shoot(ledger, self.cfg, int(shoot_id), card)
                        removed += done["removed"]
                        freed += done["freed"]
                    except (ValueError, OSError) as exc:
                        errors.append(str(exc))
        return {"error": " ".join(errors), "removed": removed, "freed": freed}

    def get_settings(self) -> dict:
        out = settings.public(self.cfg)
        out["start_at_power_on"] = poweron.is_on(getpass.getuser())
        out["power_on_ready"] = nas.installed() and poweron.GDM_CONF.parent.is_dir()
        out["fs_password"] = self.password_status()
        out["splash_available"] = nas.installed() and SPLASH_THEME.is_file()
        out["splash_on"] = bool(nas.system_state().get("splash"))
        out["splash_job"] = dict(self.splash_job)
        out["kiosk_available"] = nas.installed() and kiosk.available()
        out["kiosk_on"] = kiosk.chosen() == "kiosk"
        out["in_kiosk"] = kiosk.in_kiosk()
        out["system_setup"] = firstrun.state()
        return out

    def set_settings(self, changes: dict) -> str:
        changes = dict(changes)
        error = ""
        if changes.get("direct_cable") is False and self.direct_state.get("active"):
            direct.stop()  # switched off while linked: back to an ordinary cable
        if "start_at_power_on" in changes:
            on = changes.pop("start_at_power_on")
            if not isinstance(on, bool):
                return "Cannot set start_at_power_on."
            error = poweron.switch(self.cfg, on, getpass.getuser())
        if "boot_splash" in changes:
            on = changes.pop("boot_splash")
            if not isinstance(on, bool):
                return "Cannot set boot_splash."
            error = self.switch_splash(on) or error
        if "kiosk" in changes:
            on = changes.pop("kiosk")
            if not isinstance(on, bool):
                return "Cannot set kiosk."
            error = kiosk.switch(on) or error
        rescan = "copy_what" in changes and changes.get("copy_what") != self.cfg.get("copy_what")
        if changes:
            error = settings.apply(self.cfg, changes) or error
        if rescan and self.camera.get("state") in ("ready", "error"):
            self.scan()  # what is new on the camera depends on what is copied
        self.wake.set()
        return error

    # ----- the network ------------------------------------------------------------

    def _net_task(self, busy_text: str, work) -> bool:
        """Run a slow network change in the background and report how it went."""
        with self.lock:
            if self.net_job["busy"]:
                return False
            self.net_job = {"busy": True, "message": busy_text, "error": ""}

        def run():
            try:
                error, done_text = work()
            except Exception as exc:
                error, done_text = str(exc), ""
            with self.lock:
                self.net_job = {"busy": False, "message": "" if error else done_text, "error": error}
            self.wake.set()

        threading.Thread(target=run, name="network", daemon=True).start()
        return True

    # ----- Bluetooth -----------------------------------------------------------------

    def _bt_task(self, busy_text: str, work) -> bool:
        with self.lock:
            if self.bt_job["busy"]:
                return False
            self.bt_job = {"busy": True, "message": busy_text, "error": ""}

        def progress(text: str) -> None:
            with self.lock:
                self.bt_job["message"] = text

        def run():
            try:
                error, done_text = work(progress)
            except Exception as exc:
                error, done_text = str(exc), ""
            with self.lock:
                self.bt_job = {"busy": False, "message": "" if error else done_text, "error": error}
            self.wake.set()

        threading.Thread(target=run, name="bluetooth", daemon=True).start()
        return True

    def bt_status(self) -> dict:
        return bluetooth.status()

    def bt_power(self, on: bool) -> bool:
        return self._bt_task("Switching Bluetooth " + ("on..." if on else "off..."),
                             lambda progress: (bluetooth.power(on), ""))

    def bt_scan(self) -> bool:
        return self._bt_task("Looking for devices nearby. Put yours in pairing mode...",
                             lambda progress: (bluetooth.scan(), "Done looking."))

    def bt_pair(self, address: str, name: str) -> bool:
        return self._bt_task("Pairing with " + (name or address) + "...",
                             lambda progress: (bluetooth.pair(address, progress), "Paired with " + (name or address) + "."))

    def bt_remove(self, address: str) -> bool:
        return self._bt_task("Removing...", lambda progress: (bluetooth.remove(address), "Removed."))

    def bt_connect(self, address: str, on: bool) -> bool:
        return self._bt_task("Connecting..." if on else "Disconnecting...",
                             lambda progress: (bluetooth.connect(address, on), "Connected." if on else "Disconnected."))

    def net_scan(self) -> dict:
        return network.scan()

    def net_connect(self, ssid: str, password: str) -> bool:
        return self._net_task(f"Joining {ssid}...",
                              lambda: (network.connect(ssid, password), f"Joined {ssid}."))

    def net_disconnect(self) -> bool:
        return self._net_task("Leaving the network...", lambda: (network.disconnect(), "Left the network."))

    def hotspot_credentials(self) -> tuple:
        if not self.cfg.get("hotspot_password"):
            settings.apply(self.cfg, {"hotspot_password": network.new_password()})
        return self.cfg.get("hotspot_name") or "DoubleTake", self.cfg["hotspot_password"]

    def hotspot(self, on: bool) -> bool:
        if not on:
            return self._net_task("Stopping the hotspot...", lambda: (network.hotspot_off(), "Hotspot off."))
        name, password = self.hotspot_credentials()
        return self._net_task("Starting the hotspot...",
                              lambda: (network.hotspot_on(name, password), f"Hotspot {name} is on."))

    # ----- the NAS --------------------------------------------------------------------

    def _nas_task(self, busy_text: str, work) -> bool:
        with self.lock:
            if self.nas_job["busy"]:
                return False
            self.nas_job = {"busy": True, "message": busy_text, "error": ""}

        def run():
            try:
                error, done_text = work()
            except Exception as exc:
                error, done_text = str(exc), ""
            with self.lock:
                self.nas_job = {"busy": False, "message": "" if error else done_text, "error": error}
            self.wake.set()

        threading.Thread(target=run, name="nas", daemon=True).start()
        return True

    def nas_switch(self, what: str, on: bool) -> bool:
        label = {"nas": "The shared folder", "sftp": "SFTP"}[what]

        def work():
            answer = nas.helper(what, "on" if on else "off")
            if answer.get("ok") and on:
                nas.sync_zones(self.cfg)
            return answer.get("error", ""), f"{label} is {'on' if on else 'off'}."

        return self._nas_task(f"Switching {label.lower()} {'on' if on else 'off'}...", work)

    def nas_login(self, name: str, password: str) -> bool:
        def work():
            answer = nas.helper("set-user", name, stdin=password + "\n")
            return answer.get("error", ""), f"The share's login is now {name}."

        return self._nas_task("Saving the login...", work)

    def nas_trust(self, changes: dict) -> str:
        """Change where the NAS may be seen, and apply it to the firewall straight away."""
        error = settings.apply(self.cfg, changes)
        if error:
            return error

        def work():
            problems = nas.sync_zones(self.cfg)
            if "nas_hotspot" in changes:
                answer = nas.helper("hotspot-zone", "on" if changes["nas_hotspot"] else "off")
                if not answer.get("ok"):
                    problems.append(answer.get("error", ""))
            return (problems[0] if problems else ""), "Saved."

        self._nas_task("Applying to the firewall...", work)
        return ""

    def nas_networks(self) -> list:
        """The known networks, for the 'Allowed on' list."""
        return nas.known_networks()

    # ----- the memory card and factory reset ---------------------------------------

    def format_card(self) -> dict:
        with self.lock:
            if self.backup is not None and self.backup.get("running"):
                return {"error": "Wait for the backup to finish."}
        with self._mirror_held():
            with self._ledger() as ledger:
                return device.format_card(self.cfg, ledger)

    def card_details(self) -> dict:
        with self._ledger() as ledger:
            return device.drive_details(self.cfg, ledger)

    def card_prepare(self, agree: str) -> dict:
        """Erase the new drive and make it DoubleTake's. Only with AGREE typed on the screens."""
        if (agree or "").strip().upper() != "AGREE":
            return {"error": "Type AGREE to erase the drive."}
        with self.lock:
            if self.backup is not None and self.backup.get("running"):
                return {"error": "Wait for the backup to finish."}
        with self._mirror_held():
            with self._ledger() as ledger:
                result = device.prepare_drive(self.cfg, ledger)
        self.mirror_paused = self.mirror_blocked = False
        self.wake.set()
        return result

    def card_report(self) -> dict:
        with self._ledger() as ledger:
            return device.card_report(self.cfg, ledger)

    def reset_settings(self) -> dict:
        result = device.reset_settings(self.cfg)
        self.wake.set()
        return result

    def erase_everything(self, confirmed: bool) -> dict:
        with self.lock:
            if self.backup is not None and self.backup.get("running"):
                return {"error": "Wait for the backup to finish."}
        with self._mirror_held():
            result = device.erase_everything(self.cfg, self.library, self.data_dir, confirmed)
        if not result.get("error"):
            with self.lock:
                self.plan, self.backup = None, None
                self.mirror.update(_new_job(), state="idle", message="")
            self._was_present = False  # read the camera again if it is plugged in
        self.wake.set()
        return result

    # ----- the screen flicker fix (Intel graphics) ----------------------------------------

    def flicker_status(self) -> dict:
        if not nas.installed():
            return {"intel": False}
        answer = nas.helper("flicker", "status", timeout=60)
        return answer if answer.get("ok") else {"intel": False, "error": answer.get("error", "")}

    def set_flicker(self, on: bool) -> dict:
        """Panel Self Refresh off (on=True) or back to the system's own choice. At the next start."""
        if not nas.installed():
            return {"error": "The flicker fix needs the system setup: run doubletake setup in a terminal."}
        answer = nas.helper("flicker", "on" if on else "off", timeout=300)
        if not answer.get("ok"):
            error = answer.get("error", "Not changed.")
            return {"error": error[0].upper() + error[1:].rstrip(".") + "."}
        return answer

    # ----- the status panel (P3) --------------------------------------------------------

    def show_status(self, on: bool) -> None:
        with self.lock:
            self.status_open = bool(on)

    def status(self) -> dict:
        """Everything about the tablet on one panel: DoubleTake and the system, the photos
        on Copy 1 and Copy 2, and each battery's charge and health."""
        from . import maintenance

        release = maintenance.os_release()
        card = dict(self.card)
        copy2 = None
        with self._ledger() as ledger:
            row = ledger.db.execute(
                "SELECT COUNT(DISTINCT s.id) AS shoots, COUNT(f.id) AS photos, COALESCE(SUM(f.size),0) AS bytes "
                "FROM shoots s JOIN files f ON f.shoot_id=s.id AND f.status='verified' WHERE s.cleared IS NULL"
            ).fetchone()
            found = cards.detect(self.cfg, ledger) if card.get("state") == "ready" else None
            if found is not None and found.state == "ready" and found.card is not None:
                held = ledger.db.execute(
                    "SELECT COUNT(*) AS n, COALESCE(SUM(f.size),0) AS bytes FROM copies c "
                    "JOIN files f ON f.id=c.file_id JOIN shoots s ON s.id=f.shoot_id "
                    "WHERE c.card_id=? AND c.status='verified' AND f.status='verified' AND s.cleared IS NULL",
                    (found.card.card_id,)).fetchone()
                copy2 = {"photos": held["n"], "bytes": held["bytes"], "waiting": card.get("waiting", 0),
                         "free": card.get("free", 0), "total": card.get("total", 0), "noun": card.get("noun", "card"),
                         "label": card.get("label", "")}
        try:
            usage = shutil.disk_usage(self.library)
            free, total = usage.free, usage.total
        except OSError:
            free = total = 0
        return {
            "version": version_text(__version__),
            "system": release.get("PRETTY_NAME", ""),
            "host": maintenance.host_name(),
            "shoots": row["shoots"], "photos": row["photos"],
            "copy1": {"photos": row["photos"], "bytes": row["bytes"], "free": free, "total": total},
            "copy2": copy2,
            "card_state": card.get("state", "none"),
            "power": _tablet_battery(),
        }

    # ----- what the screens see ----------------------------------------------------

    def state(self) -> dict:
        now = time.time()
        with self.lock:
            out = {
                "version": version_text(__version__),
                "clock": clock_text(),
                "source": self.source,
                "camera": copy.deepcopy(self.camera),
                "backup": copy.deepcopy(self.backup),
                "auto": dict(self.auto, left=max(0, int(round(self.auto["at"] - time.time()))))
                        if self.auto and self.auto.get("state") == "countdown" else (dict(self.auto) if self.auto else None),
                "screen_dark": self.screen_dark,
                "auto_chime": bool(self.cfg.get("auto_chime", True)),
                "sound": dict(self.sound),
                "status_open": self.status_open,
                "edition": str(nas.system_state().get("edition") or ""),
                "level_shown": dict(self.level_shown),
                "leaving": copy.deepcopy(self.leaving),
                "mirror": copy.deepcopy(self.mirror),
                "card": copy.deepcopy(self.card),
                "format": dict(self.format),
                "shoots": copy.deepcopy(self.shoots),
                "shoot_count": self.shoot_count,
                "names": list(self.names),
                "quick_names": list(self.cfg.get("quick_names", [])),
                "tablet": dict(self.tablet),
                "power_problem": self.power_problem(),
                "look": settings.screen_theme(self.cfg)[0],
                "theme": settings.screen_theme(self.cfg)[1],
                "net": dict(self.net),
                "net_job": dict(self.net_job),
                "bt_job": dict(self.bt_job),
                "export_job": dict(self.export_job),
                "usb_job": dict(self.usb_job),
                "phone": self.phone_share.status() if self.phone_share is not None else {"on": False},
                "nas": copy.deepcopy(self.nas),
                "nas_job": dict(self.nas_job),
                "limit": self.limit,
                "problem": self.problem,
            }
            out["mirror"]["paused_by_user"] = self.mirror_paused
        for job in (out["backup"], out["mirror"]):
            if not job:
                continue
            end = job["finished_at"] if job.get("finished_at") and not _running(job) else now
            elapsed = max(0.0, end - job["started"]) if job["started"] else 0.0
            average = job["done_bytes"] / elapsed if elapsed > 0 else 0.0
            left = job["total_bytes"] - job["done_bytes"]
            job["elapsed"] = elapsed
            job["speed_avg"] = average
            job["remaining"] = left / average if average > 0 and left > 0 else 0.0
        return out


def _running(job: dict) -> bool:
    return bool(job.get("running")) or job.get("state") == "running"


def _mains_state(root: str = "/sys/class/power_supply"):
    """Is the charger plugged in: True, False, or None when the tablet has no charger entry."""
    found = None
    try:
        for supply in Path(root).iterdir():
            try:
                if (supply / "type").read_text().strip() != "Mains":
                    continue
                online = (supply / "online").read_text().strip() == "1"
            except OSError:
                continue
            found = bool(found) or online
    except OSError:
        pass
    return found


def _number(path: Path):
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _tablet_battery(root: str = "/sys/class/power_supply") -> dict:
    """The tablet's own batteries: each one, and all of them taken together.

    Rugged tablets often have two (the 5175's second is in its keyboard; the 7220 has
    hot-swap bays). The safety rules go by the total, the charge left in all of them over
    what all of them hold; each battery is shown on its own as well, so the one running
    down can be swapped. An empty bay during a swap is simply left out.
    """
    now = full = rate = 0
    packs, levels, states = [], [], []
    try:
        supplies = sorted(Path(root).iterdir())
    except OSError:
        return {}
    for supply in supplies:
        try:
            if (supply / "type").read_text().strip() != "Battery":
                continue
            if (supply / "scope").exists() and (supply / "scope").read_text().strip() == "Device":
                continue  # a mouse or pen, not the tablet
            if (supply / "present").exists() and (supply / "present").read_text().strip() == "0":
                continue  # an empty bay
            status = (supply / "status").read_text().strip()
        except OSError:
            continue
        states.append(status)
        level = _number(supply / "capacity")
        pack_now = pack_full = design = None
        for kind in ("energy", "charge"):
            got, top = _number(supply / f"{kind}_now"), _number(supply / f"{kind}_full")
            if got is not None and top:
                pack_now, pack_full = got, top
                design = _number(supply / f"{kind}_full_design")
                now, full = now + got, full + top
                flow = _number(supply / ("power_now" if kind == "energy" else "current_now"))
                rate += abs(flow or 0)
                break
        if level is None and pack_full:
            level = round(100 * pack_now / pack_full)
        if level is not None:
            levels.append(level)
        model = ""
        try:
            model = " ".join(x for x in ((supply / "manufacturer").read_text().strip() if (supply / "manufacturer").exists() else "",
                                         (supply / "model_name").read_text().strip() if (supply / "model_name").exists() else "") if x)
        except OSError:
            pass
        packs.append({"name": supply.name, "label": f"Battery {len(packs) + 1}", "level": level,
                      "status": status.lower(), "model": model,
                      "health": round(100 * pack_full / design) if pack_full and design else None,
                      "cycles": _number(supply / "cycle_count") or None})
    if not states:
        return {}
    if full:
        total = round(100 * now / full)
    elif levels:
        total = round(sum(levels) / len(levels))
    else:
        return {}
    charging = any(x in ("Charging", "Full") for x in states)
    # The charger's own word counts. A battery saying Charging is not enough: on the 5175 the
    # keyboard's battery charges the tablet's, with no charger anywhere.
    mains = _mains_state(root)
    on_power = charging if mains is None else mains
    total = max(0, min(100, total))
    # With two or more batteries, one running out while another still has charge is a swap,
    # not a stop: say which one.
    swap = ""
    if len(packs) > 1 and not on_power and total > 10:
        low = [x for x in packs if x["level"] is not None and x["level"] <= 10]
        if low:
            swap = low[0]["label"]
    # Time left, from how fast the charge is going now: to empty on battery, to full on the charger.
    minutes = None
    if rate and full:
        hours = (full - now) / rate if on_power else now / rate
        if 0 < hours < 48:
            minutes = round(hours * 60)
    return {"battery": total, "charging": charging, "batteries": len(packs), "packs": packs,
            "swap": swap, "on_power": on_power, "minutes": minutes}
