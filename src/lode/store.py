"""Content-addressed object store.

Every chunk of every file is stored exactly once, keyed by the SHA-256 of its
*uncompressed* bytes. Addressing by content gives deduplication for free: two
files that share a run of bytes share the underlying objects, and re-saving an
unchanged file writes nothing new.

On disk the store is a sharded directory tree::

    objects/ab/cdef...   # object whose digest is "abcdef..."

The two-character prefix keeps any single directory from filling up with
hundreds of thousands of entries. Objects are zlib-compressed on write and
verified against their digest on read, so silent corruption surfaces as an error
rather than bad restored data. Writes go through a temp file + atomic rename so a
crash mid-write can never leave a half-written object addressable.
"""

from __future__ import annotations

import hashlib
import os
import zlib
from pathlib import Path
from typing import Iterator

from .errors import CorruptObject, MissingObject

__all__ = ["ObjectStore", "hash_bytes"]

_SHARD_LEN = 2
_COMPRESS_LEVEL = 6


def hash_bytes(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of ``data``."""

    return hashlib.sha256(data).hexdigest()


class ObjectStore:
    """A flat, sharded, content-addressed blob store rooted at a directory."""

    def __init__(self, root: Path):
        self.root = Path(root)

    # -- layout ----------------------------------------------------------
    def _path(self, digest: str) -> Path:
        return self.root / digest[:_SHARD_LEN] / digest[_SHARD_LEN:]

    # -- queries ---------------------------------------------------------
    def has(self, digest: str) -> bool:
        """Return whether an object with ``digest`` is already stored."""

        return self._path(digest).exists()

    def size_on_disk(self, digest: str) -> int:
        """Return the compressed on-disk size of a stored object, in bytes."""

        try:
            return self._path(digest).stat().st_size
        except FileNotFoundError:
            raise MissingObject(digest) from None

    def iter_digests(self) -> Iterator[str]:
        """Yield the digest of every object currently in the store."""

        if not self.root.exists():
            return
        for shard in sorted(self.root.iterdir()):
            if not shard.is_dir() or len(shard.name) != _SHARD_LEN:
                continue
            for obj in sorted(shard.iterdir()):
                if obj.is_file():
                    yield shard.name + obj.name

    # -- writes ----------------------------------------------------------
    def put(self, data: bytes) -> str:
        """Store ``data`` and return its digest.

        Idempotent: if the object already exists nothing is written, which is
        what makes deduplication cheap. The write is atomic — content lands in a
        temp file that is ``os.replace``'d into place only once fully flushed.
        """

        digest = hash_bytes(data)
        path = self._path(digest)
        if path.exists():
            return digest

        path.parent.mkdir(parents=True, exist_ok=True)
        compressed = zlib.compress(data, _COMPRESS_LEVEL)
        tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
        try:
            with open(tmp, "wb") as handle:
                handle.write(compressed)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
        return digest

    # -- reads -----------------------------------------------------------
    def get(self, digest: str) -> bytes:
        """Return the bytes of a stored object, verifying its content hash.

        Raises :class:`MissingObject` if absent and :class:`CorruptObject` if the
        stored bytes no longer hash to ``digest``.
        """

        path = self._path(digest)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raise MissingObject(digest) from None
        try:
            data = zlib.decompress(raw)
        except zlib.error:
            raise CorruptObject(digest) from None
        if hash_bytes(data) != digest:
            raise CorruptObject(digest)
        return data

    def verify(self, digest: str) -> bool:
        """Return True if the object exists and still matches its digest.

        Never raises for a merely corrupt object — returns False instead — so
        callers can scan the whole store and collect every problem.
        """

        path = self._path(digest)
        if not path.exists():
            return False
        try:
            data = zlib.decompress(path.read_bytes())
        except zlib.error:
            return False
        return hash_bytes(data) == digest

    def delete(self, digest: str) -> bool:
        """Remove an object if present; return whether anything was deleted.

        Also prunes the shard directory once it becomes empty so a heavily
        pruned store does not leave hundreds of empty folders behind.
        """

        path = self._path(digest)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        try:
            next(iter(os.scandir(path.parent)))
        except StopIteration:
            path.parent.rmdir()
        return True
