"""What the engine needs from anything it can copy photos from.

A camera over USB, a folder, and later a card slot or the camera's Wi-Fi all
look the same to the engine: something that can describe itself, list its
files, and hand over their bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class SourceError(Exception):
    """A problem talking to the source, with a message fit to show the user."""


class NotSupported(SourceError):
    """The source cannot do this (for example, chunked reads)."""


@dataclass(frozen=True)
class SourceFile:
    folder: str  # e.g. /store_00010001/DCIM/100ND850
    name: str  # e.g. DSC_4821.NEF
    size: int  # bytes, as the source reports it
    mtime: int  # Unix time, as the source reports it

    @property
    def path(self) -> str:
        return f"{self.folder.rstrip('/')}/{self.name}"


@dataclass
class SourceInfo:
    kind: str  # "camera", "folder" or "simulated camera"
    model: str
    serial: str  # identifies this source in the ledger
    battery: str = ""
    storage: list = field(default_factory=list)  # human-readable lines


class Source:
    #: True when read_chunk works, which allows progress inside a file and resume.
    supports_chunks = False

    def open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def info(self) -> SourceInfo:
        raise NotImplementedError

    def list_files(self, progress=None) -> list[SourceFile]:
        """List every file. `progress(count)` is called now and then while listing."""
        raise NotImplementedError

    def read_chunk(self, f: SourceFile, offset: int, size: int) -> bytes:
        """Return up to `size` bytes starting at `offset`. Empty bytes at the end."""
        raise NotSupported("This source cannot read files in pieces.")

    def read_whole(self, f: SourceFile) -> bytes:
        """Return the whole file."""
        raise NotImplementedError

    def battery_now(self):
        """The battery level now, as a number from 0 to 100, or None when not known."""
        return None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False
