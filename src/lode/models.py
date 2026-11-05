"""Serializable domain objects: file entries, stats, and snapshots.

These dataclasses are the on-disk schema for a snapshot manifest. Each knows how
to round-trip through plain ``dict`` objects so the repository layer can persist
them as JSON without any bespoke (de)serialization scattered around.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

__all__ = ["FileEntry", "Stats", "Snapshot", "FILE", "DIR", "SYMLINK"]

FILE = "file"
DIR = "dir"
SYMLINK = "symlink"


@dataclass
class FileEntry:
    """One path recorded in a snapshot.

    For regular files ``chunks`` holds the ordered list of chunk digests whose
    concatenation reproduces the file. Directories carry only their mode (so an
    empty directory survives a round-trip); symlinks carry their ``target``.
    """

    path: str
    type: str
    mode: int
    size: int = 0
    mtime: float = 0.0
    chunks: List[str] = field(default_factory=list)
    target: Optional[str] = None

    @property
    def is_file(self) -> bool:
        return self.type == FILE

    @property
    def is_dir(self) -> bool:
        return self.type == DIR

    @property
    def is_symlink(self) -> bool:
        return self.type == SYMLINK

    def to_dict(self) -> dict:
        data: Dict[str, object] = {"path": self.path, "type": self.type, "mode": self.mode}
        if self.type == FILE:
            data["size"] = self.size
            data["mtime"] = self.mtime
            data["chunks"] = list(self.chunks)
        elif self.type == SYMLINK:
            data["target"] = self.target
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "FileEntry":
        return cls(
            path=data["path"],
            type=data["type"],
            mode=data.get("mode", 0),
            size=data.get("size", 0),
            mtime=data.get("mtime", 0.0),
            chunks=list(data.get("chunks", [])),
            target=data.get("target"),
        )


@dataclass
class Stats:
    """Aggregate counts for a snapshot, including what it added to the store."""

    files: int = 0
    dirs: int = 0
    symlinks: int = 0
    total_size: int = 0
    chunks: int = 0
    new_chunks: int = 0
    new_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "files": self.files,
            "dirs": self.dirs,
            "symlinks": self.symlinks,
            "total_size": self.total_size,
            "chunks": self.chunks,
            "new_chunks": self.new_chunks,
            "new_bytes": self.new_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Stats":
        return cls(
            files=data.get("files", 0),
            dirs=data.get("dirs", 0),
            symlinks=data.get("symlinks", 0),
            total_size=data.get("total_size", 0),
            chunks=data.get("chunks", 0),
            new_chunks=data.get("new_chunks", 0),
            new_bytes=data.get("new_bytes", 0),
        )


@dataclass
class Snapshot:
    """A point-in-time manifest of a directory tree."""

    id: str
    parent: Optional[str]
    message: str
    created_at: float
    entries: List[FileEntry] = field(default_factory=list)
    stats: Stats = field(default_factory=Stats)

    @property
    def short_id(self) -> str:
        return self.id[:12]

    def entry_map(self) -> Dict[str, FileEntry]:
        """Return entries keyed by path for diff/lookup."""

        return {entry.path: entry for entry in self.entries}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent": self.parent,
            "message": self.message,
            "created_at": self.created_at,
            "stats": self.stats.to_dict(),
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Snapshot":
        return cls(
            id=data["id"],
            parent=data.get("parent"),
            message=data.get("message", ""),
            created_at=data.get("created_at", 0.0),
            entries=[FileEntry.from_dict(item) for item in data.get("entries", [])],
            stats=Stats.from_dict(data.get("stats", {})),
        )
