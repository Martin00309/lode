"""Tests for the repository engine: save, restore, diff, history, and GC."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from lode.errors import (
    AlreadyInitialized,
    NotARepository,
    RestoreError,
    SnapshotNotFound,
)
from lode.repository import ADDED, DELETED, MODIFIED, Repository


def _counter_clock(start: float = 1_000_000.0):
    """A deterministic, strictly increasing clock for reproducible snapshots."""

    state = {"t": start}

    def clock() -> float:
        state["t"] += 1.0
        return state["t"]

    return clock


def _write(root: Path, relpath: str, content: bytes) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _tree_contents(root: Path) -> dict:
    """Map every file under ``root`` to its bytes, ignoring the ``.lode`` repo."""

    result = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel == ".lode" or rel.startswith(".lode/"):
            continue
        if path.is_file() and not path.is_symlink():
            result[rel] = path.read_bytes()
    return result


class RepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "work"
        self.root.mkdir()
        self.clock = _counter_clock()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def init(self) -> Repository:
        return Repository.init(self.root, clock=self.clock)


class InitAndDiscoverTests(RepositoryTestCase):
    def test_init_creates_layout(self) -> None:
        repo = self.init()
        self.assertTrue(repo.lode_dir.is_dir())
        self.assertTrue((repo.lode_dir / "objects").is_dir())
        self.assertTrue(repo.snapshots_dir.is_dir())
        self.assertTrue(repo.config_path.is_file())
        self.assertIsNone(repo.head())

    def test_double_init_raises(self) -> None:
        self.init()
        with self.assertRaises(AlreadyInitialized):
            self.init()

    def test_discover_from_subdirectory(self) -> None:
        self.init()
        nested = self.root / "a" / "b"
        nested.mkdir(parents=True)
        repo = Repository.discover(nested)
        self.assertEqual(repo.root, self.root.resolve())

    def test_discover_without_repo_raises(self) -> None:
        with self.assertRaises(NotARepository):
            Repository.discover(self.root)


class SaveAndRestoreTests(RepositoryTestCase):
    def test_save_then_restore_reproduces_tree(self) -> None:
        _write(self.root, "README.md", b"hello\n")
        _write(self.root, "src/app.py", b"print('hi')\n")
        _write(self.root, "src/data.bin", os.urandom(50_000))
        repo = self.init()
        snap = repo.save(message="initial")

        dest = Path(self._tmp.name) / "restored"
        written = repo.restore(snap, dest)
        self.assertEqual(written, 3)
        self.assertEqual(_tree_contents(dest), _tree_contents(self.root))

    def test_empty_directory_is_preserved(self) -> None:
        (self.root / "emptydir").mkdir()
        repo = self.init()
        snap = repo.save()
        dest = Path(self._tmp.name) / "restored"
        repo.restore(snap, dest)
        self.assertTrue((dest / "emptydir").is_dir())

    def test_file_mode_is_preserved(self) -> None:
        _write(self.root, "script.sh", b"#!/bin/sh\necho hi\n")
        os.chmod(self.root / "script.sh", 0o750)
        repo = self.init()
        snap = repo.save()
        dest = Path(self._tmp.name) / "restored"
        repo.restore(snap, dest)
        self.assertEqual((dest / "script.sh").stat().st_mode & 0o777, 0o750)

    def test_symlink_roundtrip(self) -> None:
        _write(self.root, "target.txt", b"data\n")
        os.symlink("target.txt", self.root / "link.txt")
        repo = self.init()
        snap = repo.save()
        dest = Path(self._tmp.name) / "restored"
        repo.restore(snap, dest)
        link = dest / "link.txt"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "target.txt")

    def test_restore_refuses_nonempty_destination(self) -> None:
        _write(self.root, "a.txt", b"a\n")
        repo = self.init()
        snap = repo.save()
        dest = Path(self._tmp.name) / "restored"
        dest.mkdir()
        (dest / "existing").write_text("x")
        with self.assertRaises(RestoreError):
            repo.restore(snap, dest)
        # With force it overwrites.
        self.assertEqual(repo.restore(snap, dest, force=True), 1)

    def test_ignored_paths_are_excluded(self) -> None:
        _write(self.root, "keep.txt", b"keep\n")
        _write(self.root, "debug.log", b"noise\n")
        _write(self.root, "build/out.o", b"obj\n")
        (self.root / ".lodeignore").write_text("*.log\nbuild/\n", encoding="utf-8")
        repo = self.init()
        snap = repo.save()
        paths = {e.path for e in snap.entries}
        self.assertIn("keep.txt", paths)
        self.assertNotIn("debug.log", paths)
        self.assertNotIn("build/out.o", paths)
        self.assertNotIn("build", paths)


class DeduplicationTests(RepositoryTestCase):
    def test_resaving_unchanged_tree_adds_nothing(self) -> None:
        _write(self.root, "a.txt", b"content a\n")
        _write(self.root, "b.txt", os.urandom(20_000))
        repo = self.init()
        repo.save(message="first")
        second = repo.save(message="second")
        # Nothing changed on disk, so the second snapshot stores no new chunks.
        self.assertEqual(second.stats.new_chunks, 0)
        self.assertEqual(second.stats.new_bytes, 0)

    def test_editing_one_file_only_stores_its_delta(self) -> None:
        _write(self.root, "big.bin", os.urandom(200_000))
        _write(self.root, "small.txt", b"unchanged\n")
        repo = self.init()
        first = repo.save()
        # Edit only the small file; the big file is untouched.
        _write(self.root, "small.txt", b"edited!!!\n")
        second = repo.save()
        self.assertGreater(first.stats.new_chunks, 0)
        # The delta is far smaller than the big file.
        self.assertLess(second.stats.new_bytes, 10_000)


class HistoryAndDiffTests(RepositoryTestCase):
    def _three_snapshots(self):
        repo = self.init()
        _write(self.root, "a.txt", b"one\n")
        s1 = repo.save(message="first")
        _write(self.root, "b.txt", b"two\n")
        s2 = repo.save(message="second")
        _write(self.root, "a.txt", b"one-edited\n")
        (self.root / "b.txt").unlink()
        s3 = repo.save(message="third")
        return repo, s1, s2, s3

    def test_snapshots_sorted_oldest_first(self) -> None:
        repo, s1, s2, s3 = self._three_snapshots()
        self.assertEqual([s.id for s in repo.snapshots()], [s1.id, s2.id, s3.id])

    def test_head_points_to_latest(self) -> None:
        repo, _s1, _s2, s3 = self._three_snapshots()
        self.assertEqual(repo.head(), s3.id)

    def test_resolve_head_and_ancestors(self) -> None:
        repo, s1, s2, s3 = self._three_snapshots()
        self.assertEqual(repo.resolve("HEAD"), s3.id)
        self.assertEqual(repo.resolve("HEAD~1"), s2.id)
        self.assertEqual(repo.resolve("HEAD~2"), s1.id)

    def test_resolve_short_prefix(self) -> None:
        repo, _s1, _s2, s3 = self._three_snapshots()
        self.assertEqual(repo.resolve(s3.id[:12]), s3.id)

    def test_resolve_unknown_raises(self) -> None:
        repo, *_ = self._three_snapshots()
        with self.assertRaises(SnapshotNotFound):
            repo.resolve("deadbeef")

    def test_resolve_past_first_snapshot_raises(self) -> None:
        repo, *_ = self._three_snapshots()
        with self.assertRaises(SnapshotNotFound):
            repo.resolve("HEAD~9")

    def test_diff_reports_add_modify_delete(self) -> None:
        repo, _s1, s2, s3 = self._three_snapshots()
        changes = {(c.status, c.path) for c in repo.diff(s2, s3)}
        self.assertIn((MODIFIED, "a.txt"), changes)
        self.assertIn((DELETED, "b.txt"), changes)

    def test_status_reflects_working_tree(self) -> None:
        repo = self.init()
        _write(self.root, "a.txt", b"one\n")
        repo.save()
        # Clean immediately after a save.
        self.assertEqual(repo.status(), [])
        # Introduce one new and one deleted path.
        _write(self.root, "c.txt", b"new\n")
        (self.root / "a.txt").unlink()
        changes = {(c.status, c.path) for c in repo.status()}
        self.assertIn((ADDED, "c.txt"), changes)
        self.assertIn((DELETED, "a.txt"), changes)


class ForgetPruneVerifyTests(RepositoryTestCase):
    def _two_snapshots(self):
        repo = self.init()
        _write(self.root, "a.txt", b"shared\n")
        s1 = repo.save(message="first")
        _write(self.root, "b.txt", os.urandom(20_000))
        s2 = repo.save(message="second")
        return repo, s1, s2

    def test_forget_moves_head_to_parent(self) -> None:
        repo, s1, s2 = self._two_snapshots()
        repo.forget(s2.id)
        self.assertEqual(repo.head(), s1.id)
        self.assertEqual([s.id for s in repo.snapshots()], [s1.id])

    def test_forget_middle_repoints_child_parent(self) -> None:
        repo = self.init()
        _write(self.root, "a.txt", b"v1\n")
        s1 = repo.save()
        _write(self.root, "a.txt", b"v2\n")
        s2 = repo.save()
        _write(self.root, "a.txt", b"v3\n")
        s3 = repo.save()
        repo.forget(s2.id)
        # s3 should now chain directly to s1 so HEAD~1 still works.
        self.assertEqual(repo.resolve("HEAD~1"), s1.id)
        self.assertEqual(repo.head(), s3.id)

    def test_prune_reclaims_unreferenced_objects(self) -> None:
        repo, s1, s2 = self._two_snapshots()
        objects_before = len(list(repo.store.iter_digests()))
        repo.forget(s2.id)
        removed, reclaimed = repo.prune()
        self.assertGreater(removed, 0)
        self.assertGreater(reclaimed, 0)
        self.assertLess(len(list(repo.store.iter_digests())), objects_before)
        # Surviving snapshot is still fully intact and restorable.
        self.assertEqual(repo.verify(), [])

    def test_prune_keeps_referenced_objects(self) -> None:
        repo, s1, s2 = self._two_snapshots()
        repo.prune()  # nothing forgotten -> nothing to remove
        self.assertEqual(repo.verify(), [])
        dest = Path(self._tmp.name) / "restored"
        repo.restore(s2, dest)
        self.assertTrue((dest / "b.txt").exists())

    def test_verify_detects_missing_object(self) -> None:
        repo, _s1, s2 = self._two_snapshots()
        digest = s2.entries[[e.path for e in s2.entries].index("b.txt")].chunks[0]
        self.assertTrue(repo.store.delete(digest))
        problems = dict(repo.verify())
        self.assertEqual(problems.get(digest), "missing")

    def test_verify_detects_corrupt_object(self) -> None:
        repo, _s1, s2 = self._two_snapshots()
        digest = s2.entries[[e.path for e in s2.entries].index("b.txt")].chunks[0]
        path = repo.store.root / digest[:2] / digest[2:]
        path.write_bytes(b"corrupted")
        problems = dict(repo.verify())
        self.assertEqual(problems.get(digest), "corrupt")


class InfoTests(RepositoryTestCase):
    def test_info_reports_counts_and_dedup(self) -> None:
        repo = self.init()
        # Incompressible payload so the metric reflects dedup, not compression.
        _write(self.root, "a.bin", os.urandom(40_000))
        repo.save()
        repo.save()  # identical second snapshot -> pure dedup
        info = repo.info()
        self.assertEqual(info["snapshots"], 2)
        self.assertGreater(info["objects"], 0)
        # Two snapshots of the same bytes => logical is ~2x physical.
        self.assertGreater(info["dedup_ratio"], 1.5)


if __name__ == "__main__":
    unittest.main()
