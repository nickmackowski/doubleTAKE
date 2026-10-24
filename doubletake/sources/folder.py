"""A plain folder as a source.

Used for testing without a camera, and later for a card in a built-in slot.
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import Source, SourceError, SourceFile, SourceInfo


class FolderSource(Source):
    supports_chunks = True

    def __init__(self, root: str):
        self.root = Path(os.path.expanduser(root)).resolve()

    def open(self) -> None:
        if not self.root.is_dir():
            raise SourceError(f"Folder not found: {self.root}")

    def info(self) -> SourceInfo:
        return SourceInfo(kind="folder", model=f"Folder {self.root}", serial=f"folder:{self.root}")

    def list_files(self, progress=None) -> list[SourceFile]:
        # If the folder looks like a camera card, only take what is under DCIM.
        base = self.root / "DCIM" if (self.root / "DCIM").is_dir() else self.root
        found = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                full = Path(dirpath) / name
                try:
                    st = full.stat()
                except OSError:
                    continue
                rel_folder = "/" + str(Path(dirpath).relative_to(self.root)).replace(os.sep, "/")
                found.append(SourceFile(rel_folder.rstrip("/") or "/", name, st.st_size, int(st.st_mtime)))
        return found

    def _full(self, f: SourceFile) -> Path:
        return self.root / f.folder.lstrip("/") / f.name

    def read_chunk(self, f: SourceFile, offset: int, size: int) -> bytes:
        try:
            with open(self._full(f), "rb") as fh:
                fh.seek(offset)
                return fh.read(size)
        except OSError as exc:
            raise SourceError(f"Could not read {f.name}: {exc}") from exc

    def read_whole(self, f: SourceFile) -> bytes:
        try:
            return self._full(f).read_bytes()
        except OSError as exc:
            raise SourceError(f"Could not read {f.name}: {exc}") from exc
