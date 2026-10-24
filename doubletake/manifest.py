"""The backup report written into each shoot folder."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import __version__
from .util import iso_time


def write_manifest(ledger, shoot_id: int) -> Path:
    shoot = ledger.shoot(shoot_id)
    files = ledger.shoot_files(shoot_id)
    runs = ledger.shoot_runs(shoot_id)
    verified = [f for f in files if f["status"] == "verified"]
    failed = [f for f in files if f["status"] == "failed"]
    data = {
        "format": 1,
        "written_by": f"doubletake {__version__}",
        "shoot": shoot["name"],
        "date": shoot["date"],
        "folder": shoot["folder"],
        "camera": {"model": shoot["source_model"], "serial": shoot["source_serial"]},
        "totals": {
            "files_verified": len(verified),
            "files_failed": len(failed),
            "bytes": sum(f["size"] for f in verified),
        },
        # Where else these files are held, each one read back and checked there.
        "second_copies": [
            {
                "card": ledger.card_label(c["card_id"]) or c["card_id"],
                "card_id": c["card_id"],
                "files_verified": c["files"],
            }
            for c in ledger.shoot_copies(shoot_id)
        ],
        "runs": [
            {
                "started": iso_time(r["started"]),
                "finished": iso_time(r["finished"]),
                "method": r["method"],
                "files_on_source": r["found"],
                "files_new": r["new"],
                "files_verified": r["verified"],
                "files_failed": r["failed"],
                "bytes": r["bytes"],
                "seconds": round(r["seconds"], 1),
                "interrupted": bool(r["interrupted"]),
                "reason": r["reason"] or "",
            }
            for r in runs
        ],
        "files": [
            {
                "name": f["rel_name"] or f["src_name"],
                "size": f["size"],
                "sha256": f["sha256"],
                "taken": iso_time(f["mtime"]),
                "source_path": f"{f['src_folder'].rstrip('/')}/{f['src_name']}",
                "status": f["status"],
                "note": f["note"] or "",
                "preview_readable": None if f["preview_ok"] is None else bool(f["preview_ok"]),
            }
            for f in files
        ],
    }
    folder = Path(ledger.library) / shoot["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "manifest.json"
    tmp = folder / ".manifest.json.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path
