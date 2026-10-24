"""Command-line interface.

    doubletake gui         open the touch screens
    doubletake doctor      check the tablet is ready
    doubletake camera      show the camera and what is new on its card
    doubletake speedtest   measure how fast the camera hands over files
    doubletake backup      copy and verify new photos, then make the second copy
    doubletake card        show the card used for the second copy
    doubletake mirror      bring the second copy up to date
    doubletake shoots      list shoots in the library
    doubletake verify      re-check a shoot against its recorded checksums
    doubletake config      show the settings in use
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, cards, checks, config
from .ingest import BackupError, Reporter, copy_choice, coverage, make_plan, run_backup
from .ledger import open_ledger
from .mirror import FULL, run_mirror, verify_card, waiting
from .sources import NotSupported, SourceError, open_source
from .util import fmt_duration, human_bytes, mb_per_s, version_text

EXIT_OK, EXIT_FAILED, EXIT_INTERRUPTED, EXIT_SOURCE = 0, 1, 2, 3


# ----- progress on the terminal -------------------------------------------------


class TerminalReporter(Reporter):
    def __init__(self):
        self.tty = sys.stdout.isatty()
        self.total_files = 0
        self.total_bytes = 0
        self.done_files = 0
        self.done_bytes = 0
        self.verified = 0
        self.failed = 0
        self.started = time.time()
        self.current = ""
        self.last_draw = 0.0
        self.width = 0

    def plan(self, plan, shoot_folder, method):
        self.total_files = len(plan.todo)
        self.total_bytes = plan.total_bytes
        self.started = time.time()
        print(f"Copying {self.total_files:,} files ({human_bytes(self.total_bytes)}) to {shoot_folder}")
        print(f"Download method: {method}")

    def mirror_start(self, count, nbytes, card):
        self.total_files = count
        self.total_bytes = nbytes
        self.started = time.time()
        print(f"Copying {count:,} files ({human_bytes(nbytes)}) to the {card.noun}, "
              "and reading each one back from it as a check")

    def file_start(self, index, f):
        self.current = f.name
        self._draw(force=True)

    def bytes(self, n):
        self.done_bytes += n
        self._draw()

    def file_done(self, f, status, note):
        self.done_files += 1
        if status == "verified":
            self.verified += 1
        else:
            self.failed += 1
            self._clear()
            print(f"  FAILED  {f.name}: {note}")
        if note and status == "verified" and note != "already present":
            self._clear()
            print(f"  note    {note}")
        if not self.tty and (self.done_files % 25 == 0 or self.done_files == self.total_files):
            print(self._line())
        self._draw(force=True)

    def _line(self) -> str:
        elapsed = time.time() - self.started
        speed = mb_per_s(self.done_bytes, elapsed)
        left = (self.total_bytes - self.done_bytes) / (self.done_bytes / elapsed) if self.done_bytes > 0 and elapsed > 0 else 0
        pct = 100 * self.done_bytes / self.total_bytes if self.total_bytes else 100
        return (
            f"[{self.done_files:>{len(str(self.total_files))}}/{self.total_files}] "
            f"{pct:5.1f}%  {human_bytes(self.done_bytes)} of {human_bytes(self.total_bytes)}  "
            f"{speed:5.1f} MB/s  elapsed {fmt_duration(elapsed)}  left ~{fmt_duration(left)}  "
            f"verified {self.verified}  failed {self.failed}  {self.current}"
        )

    def _draw(self, force=False):
        if not self.tty:
            return
        now = time.time()
        if not force and now - self.last_draw < 0.25:
            return
        self.last_draw = now
        line = self._line()
        cols = shutil.get_terminal_size((120, 20)).columns
        line = line[: cols - 1]
        pad = max(0, self.width - len(line))
        self.width = len(line)
        sys.stdout.write("\r" + line + " " * pad)
        sys.stdout.flush()

    def _clear(self):
        if self.tty and self.width:
            sys.stdout.write("\r" + " " * self.width + "\r")
            sys.stdout.flush()
            self.width = 0

    def close(self):
        if self.tty and self.width:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.width = 0


class ListingProgress:
    """Shows a running count while the card is being read, which can take a minute."""

    def __init__(self):
        self.tty = sys.stdout.isatty()
        self.shown = False

    def __call__(self, count: int) -> None:
        if self.tty:
            sys.stdout.write(f"\rReading the card... {count:,} files")
            sys.stdout.flush()
            self.shown = True

    def done(self) -> None:
        if self.shown:
            sys.stdout.write("\r" + " " * 48 + "\r")
            sys.stdout.flush()
            self.shown = False


def nikon_on_usb():
    """True if a Nikon device is visible on USB, False if not, None if we cannot tell."""
    try:
        for vendor in Path("/sys/bus/usb/devices").glob("*/idVendor"):
            if vendor.read_text().strip().lower() == "04b0":
                return True
        return False
    except OSError:
        return None


# ----- commands -----------------------------------------------------------------


def _describe(info) -> None:
    print(f"Source:   {info.model} ({info.kind})")
    if info.kind == "camera":
        print(f"Serial:   {info.serial}")
        if info.battery:
            print(f"Battery:  {info.battery}")
        for line in info.storage:
            print(f"Card:     {line}")


def _explain(found) -> None:
    """Say why there is no card to use for the second copy."""
    if found.state == "off":
        print("Second copy: switched off in the settings.")
    elif found.state == "none" and found.wanted:
        print(f"Second copy: the folder named in the settings is not there ({found.wanted}).")
    elif found.state == "none":
        print("Second copy: no card found.")
        for dev in found.unopened:
            print(f"  A card is in the tablet ({dev}) but the system has not opened it.")
            print("  Open Files and select the card. If it will not open there, it is in a")
            print("  format this system cannot read.")
    elif found.state == "declined":
        print("Second copy: a card is inserted, but you chose not to use it.")
        print("  'doubletake card use' changes that.")
    elif found.state == "several":
        print("Second copy: more than one card or drive is connected, so none was picked.")
        for c in found.all:
            print(f"  {c.mount}   {c.describe()}")
        print("  Pick one with: doubletake card use <path>")
    elif found.state == "readonly":
        print(f"Second copy: the {found.card.noun} {found.card.describe()} is read-only, so it cannot be used.")


def _offer_card(card, ledger):
    """The first time a card is seen: say what will happen, and let the user decline."""
    print(f"New {card.noun} found: {card.describe()} at {card.mount}")
    print("  It will be used as the second copy for every backup. Nothing on it is erased:")
    print(f"  photos go into a folder named {card.folder}, and everything else on it is left alone.")
    if sys.stdin.isatty():
        answer = input("  Use this card? [Y/n] ").strip().lower()
        if answer in ("n", "no"):
            cards.decline(card, ledger)
            print("  This card will not be used. 'doubletake card use' changes that.")
            return None
    try:
        cards.tag(card, ledger)
    except OSError as exc:
        print(f"  The card cannot be written to ({exc}), so it cannot hold the second copy.")
        return None
    return card


def _find_card(cfg, ledger):
    """Find the card for the second copy and say what was found. Returns it, or None."""
    found = cards.detect(cfg, ledger)
    if found.state == "ready":
        print(f"Second copy: {found.card.noun} {found.card.describe()}")
        return found.card
    if found.state == "new":
        return _offer_card(found.card, ledger)
    _explain(found)
    return None


def _mirror_and_report(cfg, ledger, card, limit=None):
    reporter = TerminalReporter()
    try:
        result = run_mirror(cfg, ledger, card, reporter=reporter, limit=limit)
    finally:
        reporter.close()
    if result.dropped:
        print(f"  {result.dropped:,} files recorded as on the {card.noun} were no longer there. They are copied again.")
    if not result.wanted:
        if result.left:
            print("SECOND COPY: nothing copied.")
        else:
            print(f"SECOND COPY UP TO DATE: the {card.noun} already holds everything that is on the tablet.")
        return result
    print()
    if result.interrupted:
        print("SECOND COPY INTERRUPTED")
        print(f"  {result.reason}")
        if result.reason != FULL:
            print("  Run 'doubletake mirror' to continue where it stopped.")
    elif result.failed:
        print("SECOND COPY FINISHED WITH PROBLEMS")
    else:
        print("SECOND COPY COMPLETE")
    print(f"  Verified:   {result.verified:,} of {result.wanted:,} files, {human_bytes(result.bytes_done)}, "
          f"each read back from the {card.noun}")
    print(f"  Failed:     {result.failed:,}")
    for fname, note in result.failures[:20]:
        print(f"              {fname}: {note}")
    print(f"  Time:       {fmt_duration(result.seconds)} at "
          f"{mb_per_s(result.bytes_done, result.seconds):.1f} MB/s average")
    if result.write_seconds > 0 and result.read_seconds > 0:
        print(f"  Speed:      wrote at {mb_per_s(result.write_bytes, result.write_seconds):.1f} MB/s, "
              f"read back at {mb_per_s(result.read_bytes, result.read_seconds):.1f} MB/s")
    if result.left:
        print(f"  Waiting:    {result.left:,} files ({human_bytes(result.left_bytes)}) still to copy")
    print(f"  Written to: {card.noun} {card.describe()}")
    return result


def _format_advice(plan, ledger) -> bool:
    """Say whether the camera's card may be formatted. Returns True when it is safe."""
    cov = coverage(plan, ledger)
    if not cov.photos:
        return False
    print(f"Photos on the camera's cards: {cov.photos:,}.  Verified on the tablet: {cov.on_tablet:,}.  "
          f"Also on Copy 2: {cov.on_second:,}.")
    if cov.safe_to_format:
        print("SAFE TO FORMAT THE CAMERA'S CARDS: every photo on them is verified in two places "
              "(Copy 1, the tablet, and Copy 2).")
        return True
    if cov.copied_safe:
        print(f"DO NOT FORMAT UNLESS YOU MEAN TO: every copied photo is verified in two places, but "
              f"{cov.skipped:,} files are not copied (the What to copy setting) and would be lost.")
        return False
    if cov.cleared:
        why = (f"{cov.cleared:,} photos on the cards were copied before and cleared from the tablet since, "
               "so they are on neither copy now (deleted from DoubleTake). Format only if you are sure they are safe elsewhere.")
    elif cov.on_tablet < cov.photos:
        n = cov.photos - cov.on_tablet
        why = f"{n:,} photo is not on the tablet yet." if n == 1 else f"{n:,} photos are not on the tablet yet."
    else:
        n = cov.photos - cov.on_second
        why = f"{n:,} photo has no second copy yet." if n == 1 else f"{n:,} photos have no second copy yet."
    print(f"DO NOT FORMAT THE CAMERA'S CARDS YET: {why}")
    return False


def cmd_camera(args, cfg) -> int:
    ledger = open_ledger(cfg)
    with open_source(args.source) as source:
        info = source.info()
        _describe(info)
        t0 = time.time()
        listing = ListingProgress()
        plan = make_plan(source, ledger, on_listing=listing, copy=copy_choice(cfg))
        listing.done()
        print(f"On card:  {plan.found:,} files (read in {time.time() - t0:.0f} s)")
        if plan.duplicates:
            print(f"          {plan.duplicates:,} are second copies of the same photo on the other card")
        print(f"Backed up already: {plan.already:,}")
        print(f"New:      {len(plan.todo):,} files, {human_bytes(plan.total_bytes)}")
        if plan.found and not plan.todo:
            print("Everything on this card is already on the tablet.")
        print()
        _format_advice(plan, ledger)
    return EXIT_OK


def cmd_speedtest(args, cfg) -> int:
    chunk = max(1, int(cfg.get("chunk_mb", 8))) * 1024 * 1024
    with open_source(args.source) as source:
        info = source.info()
        _describe(info)
        t0 = time.time()
        listing = ListingProgress()
        files = sorted(source.list_files(progress=listing), key=lambda f: (f.mtime, f.name))
        listing.done()
        print(f"Read the card's file list: {len(files):,} files in {time.time() - t0:.0f} s")
        sample, names = [], set()
        for f in files:
            # One copy of each photo is enough, even when both card slots hold it.
            if f.size > 0 and f.name not in names:
                names.add(f.name)
                sample.append(f)
            if len(sample) >= args.count:
                break
        if not sample:
            print("No files to test with.")
            return EXIT_FAILED
        total = sum(f.size for f in sample)
        print(f"Testing with {len(sample)} files, {human_bytes(total)}. Nothing is written to the tablet.\n")

        results = {}
        if source.supports_chunks:
            try:
                t0 = time.time()
                for f in sample:
                    offset = 0
                    while offset < f.size:
                        data = source.read_chunk(f, offset, min(chunk, f.size - offset))
                        if len(data) == 0:
                            break
                        offset += len(data)
                results["chunked"] = mb_per_s(total, time.time() - t0)
            except NotSupported:
                print("Reading in pieces: not supported by this source.")
        else:
            print("Reading in pieces: not supported by this source.")
        t0 = time.time()
        for f in sample:
            source.read_whole(f)
        results["whole"] = mb_per_s(total, time.time() - t0)

        for method, speed in results.items():
            label = f"Reading in pieces ({cfg.get('chunk_mb', 8)} MB)" if method == "chunked" else "Whole files"
            print(f"{label + ':':<28}{speed:7.1f} MB/s")
        best = max(results, key=results.get)
        rate = results[best]
        if rate > 0:
            print(f"\nAt {rate:.0f} MB/s, 100 GB would take about {fmt_duration(100_000 / rate)} "
                  f"and a full 256 GB card about {fmt_duration(256_000 / rate)}.")
        if len(results) == 2 and results["whole"] > results["chunked"] * 1.15:
            print('Whole files are clearly faster here. Set "download_method": "whole" in the settings file '
                  "(doubletake config shows where it is).")
    return EXIT_OK


def cmd_backup(args, cfg) -> int:
    ledger = open_ledger(cfg)
    reporter = TerminalReporter()
    result = None
    with open_source(args.source) as source:
        info = source.info()
        _describe(info)
        listing = ListingProgress()
        if not listing.tty:
            print("Reading the card...")
        plan = make_plan(source, ledger, take_all=args.all, limit=args.limit, on_listing=listing, copy=copy_choice(cfg))
        listing.done()
        both = f" On both cards: {plan.duplicates:,}." if plan.duplicates else ""
        print(f"On the camera: {plan.found:,} files.{both} Already backed up: {plan.already:,}. "
              f"To copy: {len(plan.todo):,}.")

        card = _find_card(cfg, ledger)
        if card is None:
            if plan.todo:
                print("  Without it, this backup will exist only on the tablet.")
        else:
            need = waiting(ledger, card)[1] + plan.total_bytes
            if need > card.free:
                print(f"  It is too small for everything: {human_bytes(need)} is waiting to go on it "
                      f"and {human_bytes(card.free)} is free. The newest shoots are copied first.")

        if plan.todo:
            name = args.name
            if not name:
                if sys.stdin.isatty():
                    name = input("Shoot name, or Enter to name it by the time of the first photo: ").strip()
                name = name or ""
            try:
                result = run_backup(source, cfg, ledger, name, take_all=args.all, date=args.date,
                                    limit=args.limit, reporter=reporter, plan=plan)
            except BackupError as exc:
                print(f"\nCannot start: {exc}")
                return EXIT_FAILED
            finally:
                reporter.close()
        else:
            print("Nothing to copy from the camera. Everything on it is already on the tablet.")

    # The camera has been let go of. Everything below runs from the tablet's own drive.
    if result is not None:
        speed = mb_per_s(result.bytes_done, result.seconds)
        print()
        if result.interrupted:
            print("BACKUP INTERRUPTED")
            print(f"  {result.reason}")
            print(f"  To continue into the same folder: doubletake backup --name {result.name}")
        elif result.failed:
            print("BACKUP FINISHED WITH PROBLEMS")
        else:
            print("BACKUP COMPLETE")
        print(f"  Verified:   {result.verified:,} of {len(plan.todo):,} files, {human_bytes(result.bytes_done)}")
        print(f"  Failed:     {result.failed:,}")
        for fname, note in result.failures[:20]:
            print(f"              {fname}: {note}")
        print(f"  Time:       {fmt_duration(result.seconds)} at {speed:.1f} MB/s average")
        if result.previews_ok or result.previews_bad:
            print(f"  Previews:   readable in {result.previews_ok:,} files, not found in {result.previews_bad:,}")
        print(f"  Shoot:      {result.shoot_folder}")
        print(f"  Report:     {result.manifest}")
        if result.interrupted:
            if card is not None:
                print("  The second copy was not started. It catches up on the next backup.")
            return EXIT_INTERRUPTED

    mirrored = None
    if card is not None:
        print()
        if result is not None:
            print("The camera can be unplugged. The second copy is made from the tablet's own drive.")
        mirrored = _mirror_and_report(cfg, ledger, card)

    print()
    _format_advice(plan, ledger)
    if mirrored is not None and mirrored.interrupted:
        return EXIT_INTERRUPTED
    failed = (result is not None and result.failed) or (mirrored is not None and mirrored.failed)
    return EXIT_FAILED if failed else EXIT_OK


def cmd_mirror(args, cfg) -> int:
    ledger = open_ledger(cfg)
    card = _find_card(cfg, ledger)
    if card is None:
        return EXIT_FAILED
    result = _mirror_and_report(cfg, ledger, card, limit=args.limit)
    if result.interrupted:
        return EXIT_INTERRUPTED
    return EXIT_FAILED if result.failed else EXIT_OK


def cmd_card(args, cfg) -> int:
    ledger = open_ledger(cfg)
    found = cards.detect(cfg, ledger)

    if args.action in ("use", "ignore"):
        pool = found.all
        if args.path:
            wanted = os.path.normpath(os.path.expanduser(args.path))
            pool = [c for c in pool if os.path.normpath(str(c.mount)) == wanted]
        if not pool:
            print("No card found." if not args.path else f"No card found at {args.path}.")
            return EXIT_FAILED
        if len(pool) > 1:
            print("More than one card or drive is connected. Say which one:")
            for c in pool:
                print(f"  doubletake card {args.action} {c.mount}")
            return EXIT_FAILED
        card = pool[0]
        if args.action == "ignore":
            cards.decline(card, ledger)
            print(f"The {card.noun} {card.describe()} will not be used. Nothing on it was changed.")
            return EXIT_OK
        if card.read_only:
            print(f"The {card.noun} {card.describe()} is read-only, so it cannot be used.")
            return EXIT_FAILED
        cards.allow(card, ledger)
        try:
            if not card.card_id:
                cards.tag(card, ledger)
        except OSError as exc:
            print(f"The card cannot be written to ({exc}).")
            return EXIT_FAILED
        ledger.set_card(f"id:{card.card_id}", "used", card.label)
        print(f"Using the {card.noun} {card.describe()} as the second copy.")
        print(f"Photos go into {card.root}. Nothing else on it is touched.")
        return EXIT_OK

    if found.state == "ready":
        card = found.card
        held = len(ledger.card_copies(card.card_id))
        left, left_bytes = waiting(ledger, card)
        print(f"Second copy: {card.noun} {card.describe()}")
        print(f"  Location:  {card.root}")
        print(f"  On it:     {held:,} photos, each read back and checked")
        print(f"  Waiting:   {left:,} photos ({human_bytes(left_bytes)}) to copy"
              + ("" if not left else ". Run 'doubletake mirror'."))
    elif found.state == "new":
        card = found.card
        print(f"New {card.noun} found: {card.describe()} at {card.mount}")
        print("  It has not been used yet. The next backup, or 'doubletake mirror', starts using it.")
    else:
        _explain(found)
    return EXIT_OK


def cmd_shoots(args, cfg) -> int:
    ledger = open_ledger(cfg)
    rows = ledger.list_shoots()
    if not rows:
        print(f"No shoots yet in {cfg['library']}")
        return EXIT_OK
    print(f"{'Shoot':<44}{'Files':>8}{'Failed':>8}{'2nd copy':>9}{'Size':>12}")
    for r in rows:
        cleared = "   cleared from the tablet" if r["cleared"] else ""
        print(f"{r['folder']:<44}{r['verified']:>8,}{r['failed']:>8,}{r['mirrored']:>9,}"
              f"{human_bytes(r['bytes']):>12}{cleared}")
    return EXIT_OK


def cmd_verify(args, cfg) -> int:
    library = Path(cfg["library"])
    ledger = open_ledger(cfg)
    if args.shoot:
        found = ledger.find_shoot(args.shoot)
        if found is None:
            print(f"No shoot matches '{args.shoot}'.")
            return EXIT_FAILED
        if isinstance(found, list):
            print(f"'{args.shoot}' matches more than one shoot:")
            for r in found:
                print(f"  {r['folder']}")
            return EXIT_FAILED
        shoots = [found]
    else:
        shoots = [s for s in ledger.list_shoots() if s["cleared"] is None]
    if args.card:
        found = cards.detect(cfg, ledger)
        if found.state != "ready":
            if found.state == "new":
                print("The card that is inserted has not been used for the second copy yet.")
            else:
                _explain(found)
            return EXIT_FAILED
        print(f"Reading from the {found.card.noun} {found.card.describe()}")
        bad = 0
        for shoot in shoots:
            ok, problems, absent = verify_card(ledger, found.card, shoot)
            for kind, path in problems:
                print(f"  {kind}  {path}")
            bad += len(problems)
            extra = f", {absent:,} not copied there yet" if absent else ""
            print(f"{shoot['folder']}: {ok:,} of {ok + len(problems):,} files in the second copy "
                  f"match their recorded checksums{extra}")
        if bad:
            print("Run 'doubletake mirror' to copy the missing or changed files again.")
        return EXIT_FAILED if bad else EXIT_OK

    bad = 0
    for shoot in shoots:
        files = [f for f in ledger.shoot_files(shoot["id"]) if f["status"] == "verified"]
        ok = 0
        for f in files:
            path = library / shoot["folder"] / (f["rel_name"] or f["src_name"])
            if not path.is_file():
                print(f"  MISSING  {path}")
                bad += 1
            elif checks.sha256_file(path) != f["sha256"]:
                print(f"  CHANGED  {path}")
                bad += 1
            else:
                ok += 1
        print(f"{shoot['folder']}: {ok:,} of {len(files):,} files match their recorded checksums")
    return EXIT_FAILED if bad else EXIT_OK


def cmd_move_data(args, cfg) -> int:
    from . import migrate

    plan = migrate.plan()
    if args.check:
        print(plan.message)
        return {"none": 0, "move": 10, "blocked": 11}[plan.state]
    if args.keep_old:
        migrate.keep_old()
        print(f"Kept everything in {plan.old}. The settings now point there.")
        return EXIT_OK
    if plan.state != "move":
        print(plan.message)
        return EXIT_OK if plan.state == "none" else EXIT_FAILED
    try:
        print(migrate.move(plan))
    except migrate.MoveError as exc:
        print(f"Not moved: {exc}")
        return EXIT_FAILED
    return EXIT_OK


def cmd_migrate_names(args, cfg) -> int:
    from . import rename

    notes = rename.run()
    for note in notes:
        print("  " + note)
    if not notes:
        print("  Nothing from Field Station to move over.")
    return EXIT_FAILED if any(n.startswith("NOT MOVED") for n in notes) else EXIT_OK


def cmd_gui(args, cfg) -> int:
    from . import kiosk
    from .server import serve

    from . import maintenance

    if kiosk.started_by_desktop_autostart():
        return EXIT_OK  # the kiosk session starts DoubleTake itself
    if kiosk.in_kiosk():
        if maintenance.active():
            maintenance.finish()  # back from a maintenance visit: the desktop as it was
        try:
            maintenance.write_autostart()  # ready for a visit chosen in the boot menu
        except OSError:
            pass
    elif getattr(args, "autostart", False) and maintenance.active():
        return EXIT_OK  # maintenance mode: the plain desktop, with DoubleTake closed

    return serve(cfg, source=args.source, port=args.port, browser=not args.no_browser, kiosk=not args.window,
                 limit=args.limit)


def cmd_maintenance(args, cfg) -> int:
    from . import maintenance

    return maintenance.at_desktop_login()


def cmd_tron(args, cfg) -> int:
    """Not in the help: the boot screen's other line, and a quiet Maintenance desktop."""
    from . import nas

    if not nas.installed():
        print("The system setup is needed first: doubletake setup")
        return 1
    if args.state is None:
        on = nas.helper("edition", "status").get("edition") == "tron"
        print("tron is on." if on else "tron is off.")
        return 0
    print("Rebuilding the boot screen. A couple of minutes…", flush=True)
    answer = nas.helper("edition", "tron" if args.state == "on" else "off", timeout=1800)
    if not answer.get("ok"):
        print("Not changed: " + answer.get("error", "no answer"))
        return 1
    if not answer.get("splash"):
        print("Done. The boot screen is off in Settings; it shows the line once it is on.")
    else:
        print("Done. Restart to see it.")
    return 0


def cmd_config(args, cfg) -> int:
    path = config.write_defaults_if_missing()
    print(f"Settings file: {path}")
    for key in sorted(cfg):
        print(f"  {key}: {cfg[key]}")
    return EXIT_OK


def cmd_system_setup(args, cfg) -> int:
    """After installing the package: the once-only system part alone, as the first step of setup."""
    from . import setupcli

    return 0 if setupcli.system_step(cfg, alone=True) else 1


def cmd_setup(args, cfg) -> int:
    """The setup questions, in the terminal."""
    from . import setupcli

    return setupcli.run(cfg)


def cmd_update(args, cfg) -> int:
    """System updates and a newer DoubleTake: the questions, in the terminal."""
    from . import updates

    if args.auto:
        return updates.auto(cfg)
    return updates.run(cfg, security=args.security)


def cmd_doctor(args, cfg) -> int:
    problems = 0

    def line(state, text):
        print(f"[{state:^4}] {text}")

    print(f"DoubleTake {version_text(__version__)}\n")
    pretty = ""
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for row in fh:
                if row.startswith("PRETTY_NAME="):
                    pretty = row.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    line("ok", f"System: {pretty or platform.platform()}, Python {platform.python_version()}")

    try:
        import gphoto2 as gp

        lib = gp.gp_library_version(gp.GP_VERSION_SHORT)[0]
        line("ok", f"Camera library: python-gphoto2 {gp.__version__}, libgphoto2 {lib}")
    except Exception as exc:
        line("FAIL", f"Camera library not available ({exc}). Run doubletake setup in a terminal.")
        problems += 1

    try:
        out = subprocess.run(
            ["systemctl", "--user", "is-enabled", "gvfs-gphoto2-volume-monitor.service"],
            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
        ).stdout.strip()
        if out.startswith("masked"):
            line("ok", "The desktop is stopped from grabbing the camera.")
        elif out in ("", "not-found"):
            line("ok", "The desktop's camera grabber is not installed.")
        else:
            line("warn", f"The desktop may grab the camera when it is plugged in (state: {out}). Run doubletake setup in a terminal.")
    except (OSError, subprocess.SubprocessError):
        line("warn", "Could not check whether the desktop grabs the camera.")

    library = Path(cfg["library"])
    try:
        library.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(library).free
        line("ok", f"Photos: {library}, {human_bytes(free)} free")
        line("ok", f"Records: {config.data_dir(cfg)}")
    except OSError as exc:
        line("FAIL", f"Library folder {library} cannot be used: {exc}")
        problems += 1

    try:
        found = cards.detect(cfg, open_ledger(cfg))
        if found.state == "ready":
            line("ok", f"Second copy: {found.card.noun} {found.card.describe()}")
        elif found.state == "new":
            line("ok", f"Second copy: new {found.card.noun} {found.card.describe()}. The next backup offers to use it.")
        elif found.state == "off":
            line("warn", "Second copy: switched off in the settings.")
        elif found.state == "none" and not found.unopened and not found.wanted:
            line("warn", "Second copy: no card found. Backups will exist only on the tablet.")
        else:
            line("warn", "Second copy: no usable card. 'doubletake card' explains why.")
    except Exception as exc:
        line("warn", f"Second copy: could not check for a card ({exc}).")

    try:
        with open_source(args.source) as source:
            info = source.info()
            battery = f", battery {info.battery}" if info.battery else ""
            line("ok", f"Camera: {info.model}, serial {info.serial}{battery}")
            for card in info.storage:
                line("ok", f"Card: {card}")
    except SourceError as exc:
        line("warn", f"Camera: {exc}")
        if "No camera found" in str(exc):
            seen = nikon_on_usb()
            if seen is False:
                line("warn", "The tablet sees no Nikon device on USB at all. That points to the cable, "
                             "the port, or the camera being off.")
            elif seen is True:
                line("warn", "A Nikon device is visible on USB, so the cable is fine. "
                             "The camera library could not open it.")

    print("\nReady." if not problems else f"\n{problems} problem(s) to fix first.")
    return EXIT_FAILED if problems else EXIT_OK


# ----- entry point --------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doubletake", description="Verified camera backup for a field tablet.")
    parser.add_argument("--version", action="version", version=f"doubletake {version_text(__version__)}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    def add_source(p):
        p.add_argument("--source", default="camera",
                       help="camera (default), dir:/path for a folder, or sim:/path for a simulated camera")

    p = sub.add_parser("gui", help="open the touch screens")
    add_source(p)
    p.add_argument("--window", action="store_true", help="open in a normal Firefox window instead of full screen")
    p.add_argument("--kiosk", action="store_true", help=argparse.SUPPRESS)  # the default since 0.4.5
    p.add_argument("--no-browser", action="store_true", help="start the engine without opening Firefox")
    p.add_argument("--port", type=int, help="port for the screens (default 8765)")
    p.add_argument("--limit", type=int, help="for testing: each backup copies at most this many photos")
    p.add_argument("--autostart", action="store_true", help=argparse.SUPPRESS)  # opened by the desktop at login
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("maintenance", help=argparse.SUPPRESS)  # run by the desktop at a maintenance login
    p.set_defaults(func=cmd_maintenance)

    p = sub.add_parser("tron", help=argparse.SUPPRESS)
    p.add_argument("state", nargs="?", choices=("on", "off"))
    p.set_defaults(func=cmd_tron)

    p = sub.add_parser("system-setup", help="after installing the package: finish setting up the tablet")
    p.set_defaults(func=cmd_system_setup)

    p = sub.add_parser("setup", help="set up the tablet here in the terminal: the same questions as the screens")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("update", help="look for system updates and a newer DoubleTake, and install them")
    p.add_argument("--auto", action="store_true", help=argparse.SUPPRESS)  # run at a maintenance login
    p.add_argument("--security", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("doctor", help="check the tablet is ready")
    add_source(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("camera", help="show the camera and what is new on its card")
    add_source(p)
    p.set_defaults(func=cmd_camera)

    p = sub.add_parser("speedtest", help="measure how fast the camera hands over files")
    add_source(p)
    p.add_argument("--count", type=int, default=10, help="how many files to read (default 10)")
    p.set_defaults(func=cmd_speedtest)

    p = sub.add_parser("backup", help="copy and verify new photos, then make the second copy")
    add_source(p)
    p.add_argument("--name", help="shoot name, for example Airshow")
    p.add_argument("--date", help="the date in the folder name, as YYYY-MM-DD (default: today)")
    p.add_argument("--all", action="store_true", help="copy every photo on the card, not only new ones")
    p.add_argument("--limit", type=int, help="copy at most this many files (useful for a first test)")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("card", help="show, use or ignore the card for the second copy")
    p.add_argument("action", nargs="?", default="status", choices=["status", "use", "ignore"])
    p.add_argument("path", nargs="?", help="where the card is opened, when more than one is connected")
    p.set_defaults(func=cmd_card)

    p = sub.add_parser("mirror", help="bring the second copy up to date")
    p.add_argument("--limit", type=int, help="copy at most this many files (useful for a first test)")
    p.set_defaults(func=cmd_mirror)

    p = sub.add_parser("shoots", help="list shoots in the library")
    p.set_defaults(func=cmd_shoots)

    p = sub.add_parser("verify", help="re-check shoots against their recorded checksums")
    p.add_argument("shoot", nargs="?", help="a shoot folder, or part of its name (default: all shoots)")
    p.add_argument("--card", action="store_true", help="re-check the second copy on the card instead")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("migrate-names", help=argparse.SUPPRESS)  # run by setup.sh: from the name Field Station
    p.set_defaults(func=cmd_migrate_names)
    p = sub.add_parser("move-data", help="move photos and records from an earlier version's folder")
    p.add_argument("--check", action="store_true", help="only say whether there is anything to move")
    p.add_argument("--keep-old", action="store_true", help="leave them where they are, and use them there")
    p.set_defaults(func=cmd_move_data)

    p = sub.add_parser("config", help="show the settings in use")
    p.set_defaults(func=cmd_config)
    # The commands not for the help: left out of its list.
    sub._choices_actions[:] = [a for a in sub._choices_actions if a.help != argparse.SUPPRESS]
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "date", None):
        try:
            time.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print("The date must look like 2026-10-11.")
            return EXIT_FAILED
    cfg = config.load()
    try:
        return args.func(args, cfg)
    except SourceError as exc:
        print(f"\n{exc}")
        return EXIT_SOURCE
    except KeyboardInterrupt:
        print("\nStopped.")
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
