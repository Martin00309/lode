"""The repository: the high-level engine behind every CLI command.

A repository is a ``.lode`` directory living *inside* the folder it protects::

    myproject/
        .lode/
            config.json         # format version + chunk parameters
            HEAD                # id of the most recent snapshot (or empty)
            objects/            # content-addressed chunk store
            snapshots/<id>.json # one manifest per snapshot

``save`` walks the tree, splits every file into content-defined chunks, writes
the new ones to the object store, and records a manifest that references them by
digest. Because chunks are shared across snapshots, the second and later saves
only cost the bytes that changed. The rest of the surface — ``list``, ``show``,
``diff``, ``status``, ``restore``, ``forget``, ``prune`` and ``verify`` — reads
and maintains those manifests and the store.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from . import __version__
from .chunker import AVG_SIZE, MAX_SIZE, MIN_SIZE, chunk
from .errors import (
    AlreadyInitialized,
    NotARepository,
    RestoreError,
    SnapshotNotFound,
)
from .ignore import IGNORE_FILENAME, load_ignore_file
from .models import DIR, FILE, SYMLINK, FileEntry, Snapshot, Stats
from .store import ObjectStore, hash_bytes

__all__ = ["Repository", "DiffEntry"]

LODE_DIR = ".lode"
OBJECTS_DIR = "objects"
SNAPSHOTS_DIR = "snapshots"
HEAD_FILE = "HEAD"
CONFIG_FILE = "config.json"
FORMAT_VERSION = 1

_HEAD_ALIASES = {"head", "@", "latest"}

# diff / status status codes
ADDED = "A"
MODIFIED = "M"
DELETED = "D"


class DiffEntry:
    """One line of a diff or status: a change code and the path it applies to."""

    __slots__ = ("status", "path")

    def __init__(self, status: str, path: str):
        self.status = status
        self.path = path

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, DiffEntry) and other.status == self.status and other.path == self.path
        )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"DiffEntry({self.status!r}, {self.path!r})"


class Repository:
    """An open ``.lode`` repository rooted at the directory it protects."""

    def __init__(self, root: Path, clock: Callable[[], float] = time.time):
        self.root = Path(root).resolve()
        self.lode_dir = self.root / LODE_DIR
        self.store = ObjectStore(self.lode_dir / OBJECTS_DIR)
        self._clock = clock

    # -- paths -----------------------------------------------------------
    @property
    def snapshots_dir(self) -> Path:
        return self.lode_dir / SNAPSHOTS_DIR

    @property
    def head_path(self) -> Path:
        return self.lode_dir / HEAD_FILE

    @property
    def config_path(self) -> Path:
        return self.lode_dir / CONFIG_FILE

    @property
    def exists(self) -> bool:
        return self.lode_dir.is_dir()

    # -- lifecycle -------------------------------------------------------
    @classmethod
    def init(cls, root: Path, clock: Callable[[], float] = time.time) -> "Repository":
        """Create a fresh repository under ``root``.

        Raises :class:`AlreadyInitialized` if a ``.lode`` directory is already
        present so an accidental re-init can never clobber existing snapshots.
        """

        repo = cls(root, clock=clock)
        if repo.lode_dir.exists():
            raise AlreadyInitialized(f"{repo.lode_dir} already exists")
        repo.lode_dir.mkdir(parents=True)
        (repo.lode_dir / OBJECTS_DIR).mkdir()
        repo.snapshots_dir.mkdir()
        repo.head_path.write_text("", encoding="utf-8")
        config = {
            "version": FORMAT_VERSION,
            "created_with": __version__,
            "created_at": repo._clock(),
            "chunk": {"min": MIN_SIZE, "avg": AVG_SIZE, "max": MAX_SIZE},
        }
        repo.config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        return repo

    @classmethod
    def discover(cls, start: Path, clock: Callable[[], float] = time.time) -> "Repository":
        """Find the repository at or above ``start`` by walking up for ``.lode``."""

        current = Path(start).resolve()
        for candidate in [current, *current.parents]:
            if (candidate / LODE_DIR).is_dir():
                return cls(candidate, clock=clock)
        raise NotARepository(
            f"no {LODE_DIR} repository found at or above {current} (run 'lode init')"
        )

    # -- HEAD ------------------------------------------------------------
    def head(self) -> Optional[str]:
        """Return the id of the current snapshot, or None if there are none."""

        try:
            value = self.head_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        return value or None

    def _set_head(self, snapshot_id: Optional[str]) -> None:
        self.head_path.write_text(
            (snapshot_id or "") + "\n" if snapshot_id else "", encoding="utf-8"
        )

    # -- snapshot access -------------------------------------------------
    def _all_ids(self) -> List[str]:
        if not self.snapshots_dir.exists():
            return []
        return [p.stem for p in self.snapshots_dir.glob("*.json")]

    def load_snapshot(self, snapshot_id: str) -> Snapshot:
        path = self.snapshots_dir / f"{snapshot_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise SnapshotNotFound(f"no snapshot with id {snapshot_id}") from None
        return Snapshot.from_dict(data)

    def snapshots(self) -> List[Snapshot]:
        """Return every snapshot, oldest first (ties broken by id)."""

        snaps = [self.load_snapshot(sid) for sid in self._all_ids()]
        snaps.sort(key=lambda s: (s.created_at, s.id))
        return snaps

    def resolve(self, ref: str) -> str:
        """Resolve a reference (``HEAD``, a full/short id, or ``<ref>~N``) to a full id."""

        ref = ref.strip()
        base, sep, tail = ref.partition("~")
        snapshot_id = self._resolve_base(base)
        if sep:
            steps = int(tail) if tail else 1
            for _ in range(steps):
                parent = self.load_snapshot(snapshot_id).parent
                if not parent:
                    raise SnapshotNotFound(f"{ref}: reached the first snapshot")
                snapshot_id = parent
        return snapshot_id

    def _resolve_base(self, base: str) -> str:
        if base == "" or base.lower() in _HEAD_ALIASES:
            current = self.head()
            if not current:
                raise SnapshotNotFound("HEAD: no snapshots yet")
            return current
        if (self.snapshots_dir / f"{base}.json").exists():
            return base
        matches = [sid for sid in self._all_ids() if sid.startswith(base)]
        if not matches:
            raise SnapshotNotFound(f"no snapshot matching '{base}'")
        if len(matches) > 1:
            from .errors import AmbiguousSnapshot

            raise AmbiguousSnapshot(base, matches)
        return matches[0]

    # -- tree walking ----------------------------------------------------
    def _walk(self) -> Iterator[Tuple[str, str, os.stat_result, Path]]:
        """Yield ``(relpath, kind, lstat, abspath)`` for every tracked path.

        The repository's own ``.lode`` directory and anything matched by
        ``.lodeignore`` are skipped. Directory symlinks are recorded as symlinks
        and never descended into. Output is deterministic: names are sorted at
        every level.
        """

        matcher = load_ignore_file(self.root / IGNORE_FILENAME)
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            base = Path(dirpath)
            rel_base = "" if base == self.root else base.relative_to(self.root).as_posix()
            kept: List[str] = []
            for name in sorted(dirnames):
                rel = f"{rel_base}/{name}" if rel_base else name
                full = base / name
                if rel_base == "" and name == LODE_DIR:
                    continue
                if full.is_symlink():
                    if not matcher.is_ignored(rel, True):
                        yield rel, SYMLINK, full.lstat(), full
                    continue
                if matcher.is_ignored(rel, True):
                    continue
                kept.append(name)
                yield rel, DIR, full.lstat(), full
            dirnames[:] = kept
            for name in sorted(filenames):
                rel = f"{rel_base}/{name}" if rel_base else name
                full = base / name
                if matcher.is_ignored(rel, False):
                    continue
                if full.is_symlink():
                    yield rel, SYMLINK, full.lstat(), full
                else:
                    yield rel, FILE, full.lstat(), full

    # -- save ------------------------------------------------------------
    def save(self, message: str = "", rehash: bool = False) -> Snapshot:
        """Record a snapshot of the working tree and advance HEAD.

        Unchanged files are detected by matching size, mtime and mode against the
        parent snapshot, so their chunks are reused without re-reading the file.
        Pass ``rehash=True`` to re-read and re-chunk every file regardless.
        """

        parent_id = self.head()
        parent_entries: Dict[str, FileEntry] = {}
        if parent_id:
            parent_entries = self.load_snapshot(parent_id).entry_map()

        entries: List[FileEntry] = []
        stats = Stats()
        seen_new: set = set()

        for rel, kind, st, full in self._walk():
            mode = stat.S_IMODE(st.st_mode)
            if kind == DIR:
                entries.append(FileEntry(path=rel, type=DIR, mode=mode))
                stats.dirs += 1
            elif kind == SYMLINK:
                entries.append(
                    FileEntry(path=rel, type=SYMLINK, mode=mode, target=os.readlink(full))
                )
                stats.symlinks += 1
            else:
                entry = self._save_file(
                    rel, st, mode, full, parent_entries, rehash, seen_new, stats
                )
                entries.append(entry)
                stats.files += 1
                stats.total_size += entry.size
                stats.chunks += len(entry.chunks)

        entries.sort(key=lambda e: e.path)
        created_at = self._clock()
        snapshot_id = _compute_id(parent_id, message, created_at, entries)
        snapshot = Snapshot(
            id=snapshot_id,
            parent=parent_id,
            message=message,
            created_at=created_at,
            entries=entries,
            stats=stats,
        )
        self._write_snapshot(snapshot)
        self._set_head(snapshot_id)
        return snapshot

    def _save_file(
        self,
        rel: str,
        st: os.stat_result,
        mode: int,
        full: Path,
        parent_entries: Dict[str, FileEntry],
        rehash: bool,
        seen_new: set,
        stats: Stats,
    ) -> FileEntry:
        size = st.st_size
        mtime = st.st_mtime
        previous = parent_entries.get(rel)
        if (
            not rehash
            and previous is not None
            and previous.is_file
            and previous.size == size
            and previous.mode == mode
            and previous.mtime == mtime
        ):
            # Unchanged by the cheap heuristic: reuse the recorded chunk list.
            return FileEntry(
                path=rel, type=FILE, mode=mode, size=size, mtime=mtime, chunks=list(previous.chunks)
            )

        digests: List[str] = []
        data = full.read_bytes()
        for piece in chunk(data):
            digest = hash_bytes(piece.data)
            if not self.store.has(digest) and digest not in seen_new:
                self.store.put(piece.data)
                seen_new.add(digest)
                stats.new_chunks += 1
                stats.new_bytes += len(piece.data)
            digests.append(digest)
        return FileEntry(path=rel, type=FILE, mode=mode, size=size, mtime=mtime, chunks=digests)

    def _write_snapshot(self, snapshot: Snapshot) -> None:
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        path = self.snapshots_dir / f"{snapshot.id}.json"
        path.write_text(json.dumps(snapshot.to_dict(), indent=2) + "\n", encoding="utf-8")

    # -- diff / status ---------------------------------------------------
    def diff(self, old: Snapshot, new: Snapshot) -> List[DiffEntry]:
        """Return the changes that turn snapshot ``old`` into snapshot ``new``."""

        return _diff_maps(old.entry_map(), new.entry_map())

    def status(self) -> List[DiffEntry]:
        """Return changes in the working tree relative to HEAD.

        Like ``save``'s fast path, modification is judged by size/mtime/mode, so a
        content edit that preserves all three (rare, and only within filesystem
        mtime granularity) is not reported. ``save --rehash`` always re-reads.
        """

        head_id = self.head()
        baseline = self.load_snapshot(head_id).entry_map() if head_id else {}
        current: Dict[str, FileEntry] = {}
        for rel, kind, st, full in self._walk():
            mode = stat.S_IMODE(st.st_mode)
            if kind == DIR:
                current[rel] = FileEntry(path=rel, type=DIR, mode=mode)
            elif kind == SYMLINK:
                current[rel] = FileEntry(
                    path=rel, type=SYMLINK, mode=mode, target=os.readlink(full)
                )
            else:
                current[rel] = FileEntry(
                    path=rel, type=FILE, mode=mode, size=st.st_size, mtime=st.st_mtime
                )
        return _diff_maps(baseline, current, content_aware=False)

    # -- restore ---------------------------------------------------------
    def restore(self, snapshot: Snapshot, dest: Path, force: bool = False) -> int:
        """Materialize ``snapshot`` under ``dest``; return the number of files written.

        ``dest`` must be empty (or absent) unless ``force`` is set, so a restore
        never silently overwrites an existing tree.
        """

        dest = Path(dest)
        if dest.exists() and any(dest.iterdir()) and not force:
            raise RestoreError(f"destination {dest} is not empty (use --force to overwrite)")
        dest.mkdir(parents=True, exist_ok=True)

        ordered = sorted(snapshot.entries, key=lambda e: e.path)
        dirs = [e for e in ordered if e.is_dir]
        for entry in dirs:
            (dest / entry.path).mkdir(parents=True, exist_ok=True)

        files_written = 0
        for entry in ordered:
            target = dest / entry.path
            if entry.is_dir:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.is_symlink:
                if target.exists() or target.is_symlink():
                    target.unlink()
                os.symlink(entry.target or "", target)
                continue
            with open(target, "wb") as handle:
                for digest in entry.chunks:
                    handle.write(self.store.get(digest))
            os.chmod(target, entry.mode)
            if entry.mtime:
                os.utime(target, (entry.mtime, entry.mtime))
            files_written += 1

        # Apply directory modes last so a read-only mode cannot block writing the
        # files inside it. Deepest-first keeps parents traversable until the end.
        for entry in sorted(dirs, key=lambda e: e.path, reverse=True):
            os.chmod(dest / entry.path, entry.mode)
        return files_written

    # -- forget / prune / verify ----------------------------------------
    def forget(self, snapshot_id: str) -> None:
        """Delete a snapshot manifest and repair lineage around it.

        Children that pointed at the removed snapshot are repointed to its parent
        so ``~N`` traversal stays intact, and HEAD is moved back to the parent if
        it referenced the removed snapshot.
        """

        target = self.load_snapshot(snapshot_id)
        for other in self.snapshots():
            if other.id != snapshot_id and other.parent == snapshot_id:
                other.parent = target.parent
                self._write_snapshot(other)
        (self.snapshots_dir / f"{snapshot_id}.json").unlink()
        if self.head() == snapshot_id:
            if target.parent and (self.snapshots_dir / f"{target.parent}.json").exists():
                self._set_head(target.parent)
            else:
                remaining = self.snapshots()
                self._set_head(remaining[-1].id if remaining else None)

    def reachable_chunks(self) -> set:
        """Return the set of chunk digests referenced by any snapshot."""

        reachable: set = set()
        for snap in self.snapshots():
            for entry in snap.entries:
                reachable.update(entry.chunks)
        return reachable

    def prune(self) -> Tuple[int, int]:
        """Delete unreferenced objects; return (objects removed, bytes reclaimed)."""

        reachable = self.reachable_chunks()
        removed = 0
        reclaimed = 0
        for digest in list(self.store.iter_digests()):
            if digest not in reachable:
                reclaimed += self.store.size_on_disk(digest)
                if self.store.delete(digest):
                    removed += 1
        return removed, reclaimed

    def verify(self) -> List[Tuple[str, str]]:
        """Check every referenced chunk; return a list of ``(digest, problem)``.

        ``problem`` is ``"missing"`` if the object is absent or ``"corrupt"`` if it
        is present but no longer hashes to its digest. An empty list means the
        repository is intact.
        """

        problems: List[Tuple[str, str]] = []
        for digest in sorted(self.reachable_chunks()):
            if not self.store.has(digest):
                problems.append((digest, "missing"))
            elif not self.store.verify(digest):
                problems.append((digest, "corrupt"))
        return problems

    # -- info ------------------------------------------------------------
    def info(self) -> dict:
        """Return repository-wide statistics for ``lode info``."""

        snaps = self.snapshots()
        objects = list(self.store.iter_digests())
        physical = sum(self.store.size_on_disk(d) for d in objects)
        logical = sum(s.stats.total_size for s in snaps)
        latest = snaps[-1] if snaps else None
        return {
            "root": str(self.root),
            "snapshots": len(snaps),
            "objects": len(objects),
            "physical_bytes": physical,
            "logical_bytes": logical,
            "dedup_ratio": (logical / physical) if physical else 0.0,
            "head": self.head(),
            "latest": latest.id if latest else None,
        }


# -- module helpers ------------------------------------------------------
def _entries_differ(a: FileEntry, b: FileEntry, content_aware: bool) -> bool:
    if a.type != b.type:
        return True
    if a.type == SYMLINK:
        return a.target != b.target
    if a.type == DIR:
        return a.mode != b.mode
    # file
    if a.mode != b.mode or a.size != b.size:
        return True
    if content_aware:
        return a.chunks != b.chunks
    return a.mtime != b.mtime


def _diff_maps(
    old: Dict[str, FileEntry],
    new: Dict[str, FileEntry],
    content_aware: bool = True,
) -> List[DiffEntry]:
    changes: List[DiffEntry] = []
    for path in new.keys() - old.keys():
        changes.append(DiffEntry(ADDED, path))
    for path in old.keys() - new.keys():
        changes.append(DiffEntry(DELETED, path))
    for path in old.keys() & new.keys():
        if _entries_differ(old[path], new[path], content_aware):
            changes.append(DiffEntry(MODIFIED, path))
    changes.sort(key=lambda c: c.path)
    return changes


def _compute_id(
    parent: Optional[str],
    message: str,
    created_at: float,
    entries: Iterable[FileEntry],
) -> str:
    """Derive a snapshot id from its full content (parent, message, time, tree).

    Including the parent guarantees that re-saving an identical tree still yields
    a distinct id, since the parent link differs each time.
    """

    payload = json.dumps(
        {
            "parent": parent,
            "message": message,
            "created_at": created_at,
            "entries": [e.to_dict() for e in entries],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hash_bytes(payload.encode("utf-8"))
