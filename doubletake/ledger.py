"""The record of what has been backed up.

A small SQLite database inside the library. It is what lets a backup copy
only new photos, and what the shoot library and later copies are checked
against.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS shoots (
    id            INTEGER PRIMARY KEY,
    folder        TEXT NOT NULL UNIQUE,   -- relative to the library
    name          TEXT NOT NULL,
    date          TEXT NOT NULL,          -- YYYY-MM-DD
    created       REAL NOT NULL,
    source_model  TEXT,
    source_serial TEXT
);
CREATE TABLE IF NOT EXISTS files (
    id         INTEGER PRIMARY KEY,
    shoot_id   INTEGER NOT NULL REFERENCES shoots(id),
    src_serial TEXT NOT NULL,
    src_folder TEXT NOT NULL,
    src_name   TEXT NOT NULL,
    size       INTEGER NOT NULL,
    mtime      INTEGER NOT NULL,
    sha256     TEXT,
    rel_name   TEXT,                      -- file name inside the shoot folder
    status     TEXT NOT NULL,             -- 'verified' or 'failed'
    note       TEXT,
    preview_ok INTEGER,                   -- 1, 0 or NULL when not checked
    copied     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS files_identity ON files (src_serial, src_name, size, mtime);
CREATE INDEX IF NOT EXISTS files_shoot ON files (shoot_id);
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY,
    shoot_id      INTEGER REFERENCES shoots(id),
    started       REAL NOT NULL,
    finished      REAL NOT NULL,
    source_model  TEXT,
    source_serial TEXT,
    method        TEXT,
    found         INTEGER,
    new           INTEGER,
    verified      INTEGER,
    failed        INTEGER,
    bytes         INTEGER,
    seconds       REAL,
    interrupted   INTEGER,
    reason        TEXT
);
-- Cards used for the second copy, and cards the user chose not to use.
CREATE TABLE IF NOT EXISTS cards (
    key        TEXT PRIMARY KEY,          -- 'id:<card id>' when used, the card's own identity when declined
    state      TEXT NOT NULL,             -- 'used' or 'declined'
    label      TEXT,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL
);
-- One row per file per card: the second copy.
CREATE TABLE IF NOT EXISTS pending_renames (
    file_id INTEGER NOT NULL REFERENCES files(id),
    card_id TEXT NOT NULL,
    old_rel TEXT NOT NULL,                -- the name the copy on that card still has
    PRIMARY KEY (file_id, card_id)
);
CREATE TABLE IF NOT EXISTS copies (
    file_id INTEGER NOT NULL REFERENCES files(id),
    card_id TEXT NOT NULL,
    status  TEXT NOT NULL,                -- 'verified' or 'failed'
    note    TEXT,
    copied  REAL NOT NULL,
    PRIMARY KEY (file_id, card_id)
);
"""


class Ledger:
    def __init__(self, library: Path, data_dir=None):
        self.library = Path(library)
        # The records live apart from the photos (Documents/DoubleTake), or inside
        # the library when no other place is given.
        self.dir = Path(data_dir) if data_dir else self.library / ".doubletake"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.dir / "ledger.sqlite")
        self.db.row_factory = sqlite3.Row
        # Favour durability over speed: a record is only useful if it survives a power cut.
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        # Added in 0.4: when a shoot was marked as imported, and when it
        # was cleared off the tablet. A cleared shoot keeps its records, so its photos
        # are still recognised as backed up if they are still on the camera.
        have = {row[1] for row in self.db.execute("PRAGMA table_info(shoots)")}
        for column in ("imported", "cleared"):
            if column not in have:
                self.db.execute(f"ALTER TABLE shoots ADD COLUMN {column} REAL")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ----- "have we got this already?" -----------------------------------

    # A camera's card in a reader has its own identity ("card:..."), not the camera's. So
    # that a card read once through the camera and once in a reader is not copied twice,
    # a file from a card matches a file from any source, and a file from a camera also
    # matches one read from a card. Same name, same size to the byte and the same time:
    # two different photos do not share all three.
    SOURCE_MATCH = "(src_serial=? OR src_serial LIKE 'card:%' OR ? LIKE 'card:%')"

    def seen(self, serial: str, name: str, size: int, mtime: int, within: int = 0) -> bool:
        """Has this file been backed up? `within` allows the time to differ by a few
        seconds, which happens when the same shot sits on both card slots."""
        row = self.db.execute(
            f"SELECT 1 FROM files WHERE {self.SOURCE_MATCH} AND src_name=? AND size=? AND mtime BETWEEN ? AND ? "
            "AND status='verified' LIMIT 1",
            (serial, serial, name, size, mtime - within, mtime + within),
        ).fetchone()
        return row is not None

    # ----- shoots ---------------------------------------------------------

    def get_or_create_shoot(self, folder: str, name: str, date: str, model: str, serial: str) -> int:
        row = self.db.execute("SELECT id FROM shoots WHERE folder=?", (folder,)).fetchone()
        if row:
            return row["id"]
        cur = self.db.execute(
            "INSERT INTO shoots (folder, name, date, created, source_model, source_serial) "
            "VALUES (?,?,?,?,?,?)",
            (folder, name, date, time.time(), model, serial),
        )
        self.db.commit()
        return cur.lastrowid

    def shoot(self, shoot_id: int):
        return self.db.execute("SELECT * FROM shoots WHERE id=?", (shoot_id,)).fetchone()

    def find_shoot(self, text: str):
        """Find a shoot by its folder, or by any part of its folder name."""
        row = self.db.execute("SELECT * FROM shoots WHERE folder=?", (text,)).fetchone()
        if row:
            return row
        rows = self.db.execute(
            "SELECT * FROM shoots WHERE folder LIKE ? ORDER BY date DESC, id DESC", (f"%{text}%",)
        ).fetchall()
        return rows[0] if len(rows) == 1 else (rows or None)

    def list_shoots(self):
        return self.db.execute(
            "SELECT s.*, "
            " (SELECT COUNT(*) FROM files f WHERE f.shoot_id=s.id AND f.status='verified') AS verified, "
            " (SELECT COUNT(*) FROM files f WHERE f.shoot_id=s.id AND f.status='failed') AS failed, "
            " (SELECT COALESCE(SUM(size),0) FROM files f WHERE f.shoot_id=s.id AND f.status='verified') AS bytes, "
            " (SELECT COUNT(*) FROM files f WHERE f.shoot_id=s.id AND f.status='verified' AND EXISTS "
            "   (SELECT 1 FROM copies c WHERE c.file_id=f.id AND c.status='verified')) AS mirrored "
            "FROM shoots s ORDER BY s.date DESC, s.id DESC"
        ).fetchall()

    # ----- files ----------------------------------------------------------

    def add_file(self, shoot_id, serial, f, sha256, rel_name, status, note="", preview_ok=None) -> None:
        # Copying everything again must not list the same photo twice.
        if status == "verified":
            same = self.db.execute(
                "SELECT 1 FROM files WHERE shoot_id=? AND src_serial=? AND src_name=? AND size=? AND mtime=? "
                "AND status='verified' AND sha256=? LIMIT 1",
                (shoot_id, serial, f.name, f.size, f.mtime, sha256),
            ).fetchone()
            if same:
                return
        # A file that failed earlier and now succeeds replaces its failed record.
        self.db.execute(
            "DELETE FROM files WHERE shoot_id=? AND src_serial=? AND src_name=? AND size=? AND mtime=? "
            "AND status='failed'",
            (shoot_id, serial, f.name, f.size, f.mtime),
        )
        self.db.execute(
            "INSERT INTO files (shoot_id, src_serial, src_folder, src_name, size, mtime, sha256, rel_name, "
            "status, note, preview_ok, copied) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                shoot_id,
                serial,
                f.folder,
                f.name,
                f.size,
                f.mtime,
                sha256,
                rel_name,
                status,
                note,
                None if preview_ok is None else int(bool(preview_ok)),
                time.time(),
            ),
        )
        self.db.commit()

    def shoot_files(self, shoot_id: int):
        return self.db.execute(
            "SELECT * FROM files WHERE shoot_id=? ORDER BY mtime, src_name, id", (shoot_id,)
        ).fetchall()

    # ----- runs -----------------------------------------------------------

    def add_run(self, shoot_id, started, finished, model, serial, method, found, new, verified, failed,
                nbytes, seconds, interrupted, reason) -> None:
        self.db.execute(
            "INSERT INTO runs (shoot_id, started, finished, source_model, source_serial, method, found, new, "
            "verified, failed, bytes, seconds, interrupted, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (shoot_id, started, finished, model, serial, method, found, new, verified, failed, nbytes,
             seconds, int(bool(interrupted)), reason),
        )
        self.db.commit()

    def shoot_runs(self, shoot_id: int):
        return self.db.execute("SELECT * FROM runs WHERE shoot_id=? ORDER BY started", (shoot_id,)).fetchall()

    # ----- second copies ----------------------------------------------------

    def card_state(self, key: str):
        row = self.db.execute("SELECT state FROM cards WHERE key=?", (key,)).fetchone()
        return row["state"] if row else None

    def card_label(self, card_id: str) -> str:
        row = self.db.execute("SELECT label FROM cards WHERE key=?", (f"id:{card_id}",)).fetchone()
        return (row["label"] if row else "") or ""

    def set_card(self, key: str, state: str, label: str = "") -> None:
        now = time.time()
        self.db.execute(
            "INSERT INTO cards (key, state, label, first_seen, last_seen) VALUES (?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET state=excluded.state, label=excluded.label, "
            "last_seen=excluded.last_seen",
            (key, state, label, now, now),
        )
        self.db.commit()

    def clear_card(self, key: str) -> None:
        self.db.execute("DELETE FROM cards WHERE key=?", (key,))
        self.db.commit()

    def pending_copies(self, card_id: str):
        """Verified files on the tablet that this card does not hold yet, newest shoot first."""
        return self.db.execute(
            "SELECT f.*, s.folder AS shoot_folder FROM files f JOIN shoots s ON s.id=f.shoot_id "
            "WHERE f.status='verified' AND s.cleared IS NULL AND NOT EXISTS "
            " (SELECT 1 FROM copies c WHERE c.file_id=f.id AND c.card_id=? AND c.status='verified') "
            "ORDER BY s.date DESC, s.id DESC, f.mtime, f.src_name, f.id",
            (card_id,),
        ).fetchall()

    def card_copies(self, card_id: str, shoot_id=None):
        """Files this card is recorded as holding."""
        sql = ("SELECT f.*, s.folder AS shoot_folder FROM copies c JOIN files f ON f.id=c.file_id "
               "JOIN shoots s ON s.id=f.shoot_id WHERE c.card_id=? AND c.status='verified' "
               "AND f.status='verified' AND s.cleared IS NULL")
        args = [card_id]
        if shoot_id is not None:
            sql += " AND f.shoot_id=?"
            args.append(shoot_id)
        return self.db.execute(sql + " ORDER BY s.date DESC, s.id DESC, f.mtime, f.src_name, f.id", args).fetchall()

    def copy_totals(self, card_id: str):
        """(files held by this card, files still waiting, bytes still waiting)."""
        row = self.db.execute(
            "SELECT COUNT(*) AS files, COALESCE(SUM(f.size),0) AS bytes, "
            " COALESCE(SUM(CASE WHEN c.file_id IS NULL THEN 0 ELSE 1 END),0) AS held, "
            " COALESCE(SUM(CASE WHEN c.file_id IS NULL THEN f.size ELSE 0 END),0) AS waiting_bytes "
            "FROM files f JOIN shoots s ON s.id=f.shoot_id "
            "LEFT JOIN copies c ON c.file_id=f.id AND c.card_id=? AND c.status='verified' "
            "WHERE f.status='verified' AND s.cleared IS NULL",
            (card_id,),
        ).fetchone()
        return row["held"], row["files"] - row["held"], row["waiting_bytes"]

    def forget_card(self, card_id: str) -> None:
        """A card was formatted: it holds none of the photos any more."""
        self.db.execute("DELETE FROM copies WHERE card_id=?", (card_id,))
        self.db.execute("DELETE FROM cards WHERE key=?", (f"id:{card_id}",))
        self.db.commit()

    def unimported_shoots(self) -> int:
        row = self.db.execute("SELECT COUNT(*) AS n FROM shoots WHERE imported IS NULL AND cleared IS NULL "
                              "AND EXISTS (SELECT 1 FROM files f WHERE f.shoot_id=shoots.id AND f.status='verified')"
                              ).fetchone()
        return row["n"]

    def recent_names(self, limit: int = 4):
        rows = self.db.execute("SELECT name FROM shoots GROUP BY name ORDER BY MAX(id) DESC LIMIT ?", (limit,))
        return [r["name"] for r in rows]

    def set_copy(self, file_id: int, card_id: str, status: str, note: str = "", commit: bool = True) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO copies (file_id, card_id, status, note, copied) VALUES (?,?,?,?,?)",
            (file_id, card_id, status, note, time.time()),
        )
        if commit:
            self.db.commit()

    def drop_copy(self, file_id: int, card_id: str, commit: bool = True) -> None:
        self.db.execute("DELETE FROM copies WHERE file_id=? AND card_id=?", (file_id, card_id))
        if commit:
            self.db.commit()

    def commit(self) -> None:
        self.db.commit()

    def shoot_copies(self, shoot_id: int):
        """How many of a shoot's files each card holds."""
        return self.db.execute(
            "SELECT c.card_id, COUNT(*) AS files FROM copies c JOIN files f ON f.id=c.file_id "
            "WHERE f.shoot_id=? AND f.status='verified' AND c.status='verified' GROUP BY c.card_id",
            (shoot_id,),
        ).fetchall()

    def copy_state(self, serial: str, name: str, size: int, mtime: int, within: int = 0):
        """For a file on the camera: (on Copy 1?, on Copy 2 as well?, cleared?).

        Strictly the two copies: a shoot marked as imported counts for nothing here; what
        happens after DoubleTake's two copies is the photographer's. Cleared = it was
        backed up once, then cleared from the tablet, so it is on neither copy now.
        """
        rows = self.db.execute(
            "SELECT EXISTS (SELECT 1 FROM copies c WHERE c.file_id=f.id AND c.status='verified') AS mirrored, "
            "s.cleared AS cleared "
            "FROM files f JOIN shoots s ON s.id=f.shoot_id WHERE "
            + self.SOURCE_MATCH.replace("src_serial", "f.src_serial") +
            " AND f.src_name=? AND f.size=? AND f.mtime BETWEEN ? AND ? AND f.status='verified'",
            (serial, serial, name, size, mtime - within, mtime + within),
        ).fetchall()
        kept = [r for r in rows if not r["cleared"]]
        return bool(kept), any(r["mirrored"] for r in kept), bool(rows) and not kept

    # ----- imported, and clearing ---------------------------------------------

    def set_imported(self, shoot_id: int, when) -> None:
        self.db.execute("UPDATE shoots SET imported=? WHERE id=? AND cleared IS NULL", (when, shoot_id))
        self.db.commit()

    def set_cleared(self, shoot_id: int, when) -> None:
        self.db.execute("UPDATE shoots SET cleared=? WHERE id=?", (when, shoot_id))
        self.db.commit()

    def cover(self, shoot_id: int):
        """The id of a shoot's first photo, to show as its picture."""
        row = self.db.execute(
            "SELECT id FROM files WHERE shoot_id=? AND status='verified' ORDER BY mtime, src_name, id LIMIT 1",
            (shoot_id,),
        ).fetchone()
        return row["id"] if row else None

    def file_by_id(self, file_id: int):
        return self.db.execute(
            "SELECT f.*, s.folder AS shoot_folder, s.cleared AS cleared FROM files f "
            "JOIN shoots s ON s.id=f.shoot_id WHERE f.id=?",
            (file_id,),
        ).fetchone()

    # ----- renaming ---------------------------------------------------------------

    def card_ids_of_file(self, file_id: int) -> list:
        rows = self.db.execute("SELECT card_id FROM copies WHERE file_id=? AND status='verified'", (file_id,))
        return [r["card_id"] for r in rows]

    def set_rel_name(self, file_id: int, rel_name: str, commit: bool = True) -> None:
        self.db.execute("UPDATE files SET rel_name=? WHERE id=?", (rel_name, file_id))
        if commit:
            self.db.commit()

    def add_pending_rename(self, file_id: int, card_id: str, old_rel: str) -> None:
        # A file renamed twice while the card is out still has its first name there.
        self.db.execute("INSERT OR IGNORE INTO pending_renames (file_id, card_id, old_rel) VALUES (?,?,?)",
                        (file_id, card_id, old_rel))

    def pending_renames(self, card_id: str):
        return self.db.execute(
            "SELECT p.file_id, p.old_rel, f.rel_name, f.src_name, s.folder AS shoot_folder, s.id AS shoot_id "
            "FROM pending_renames p JOIN files f ON f.id=p.file_id JOIN shoots s ON s.id=f.shoot_id "
            "WHERE p.card_id=? ORDER BY s.id, f.id", (card_id,)).fetchall()

    def drop_pending_rename(self, file_id: int, card_id: str) -> None:
        self.db.execute("DELETE FROM pending_renames WHERE file_id=? AND card_id=?", (file_id, card_id))
        self.db.commit()

    def copies_of_shoot(self, shoot_id: int, card_id: str):
        return self.db.execute(
            "SELECT f.* FROM copies c JOIN files f ON f.id=c.file_id WHERE f.shoot_id=? AND c.card_id=?",
            (shoot_id, card_id),
        ).fetchall()

    def cleared_with_copies(self, card_id: str):
        """Shoots cleared off the tablet whose second copy is still on this card."""
        return self.db.execute(
            "SELECT DISTINCT s.* FROM shoots s JOIN files f ON f.shoot_id=s.id JOIN copies c ON c.file_id=f.id "
            "WHERE s.cleared IS NOT NULL AND c.card_id=?",
            (card_id,),
        ).fetchall()


def open_ledger(cfg: dict) -> Ledger:
    """The record for a set of settings."""
    return Ledger(Path(cfg["library"]), cfg.get("data_dir") or None)
