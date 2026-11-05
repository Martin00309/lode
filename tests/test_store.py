"""Tests for the content-addressed object store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lode.errors import CorruptObject, MissingObject
from lode.store import ObjectStore, hash_bytes


class ObjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ObjectStore(Path(self._tmp.name) / "objects")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_put_get_roundtrip(self) -> None:
        data = b"the quick brown fox" * 100
        digest = self.store.put(data)
        self.assertEqual(digest, hash_bytes(data))
        self.assertTrue(self.store.has(digest))
        self.assertEqual(self.store.get(digest), data)

    def test_put_is_deduplicated(self) -> None:
        data = b"same bytes"
        first = self.store.put(data)
        second = self.store.put(data)
        self.assertEqual(first, second)
        self.assertEqual(list(self.store.iter_digests()), [first])

    def test_sharded_layout(self) -> None:
        digest = self.store.put(b"layout")
        expected = self.store.root / digest[:2] / digest[2:]
        self.assertTrue(expected.is_file())

    def test_get_missing_raises(self) -> None:
        with self.assertRaises(MissingObject):
            self.store.get("0" * 64)

    def test_size_on_disk_reports_compressed_size(self) -> None:
        digest = self.store.put(b"x" * 10_000)
        size = self.store.size_on_disk(digest)
        self.assertGreater(size, 0)
        # Highly compressible input should shrink on disk.
        self.assertLess(size, 10_000)

    def test_corruption_is_detected_on_get(self) -> None:
        digest = self.store.put(b"important data")
        path = self.store.root / digest[:2] / digest[2:]
        path.write_bytes(b"not valid zlib")
        with self.assertRaises(CorruptObject):
            self.store.get(digest)
        self.assertFalse(self.store.verify(digest))

    def test_verify_true_for_intact_object(self) -> None:
        digest = self.store.put(b"intact")
        self.assertTrue(self.store.verify(digest))

    def test_verify_false_for_missing_object(self) -> None:
        self.assertFalse(self.store.verify("a" * 64))

    def test_delete_removes_object_and_empty_shard(self) -> None:
        digest = self.store.put(b"temporary")
        shard = self.store.root / digest[:2]
        self.assertTrue(self.store.delete(digest))
        self.assertFalse(self.store.has(digest))
        self.assertFalse(shard.exists())
        # Deleting again is a no-op that reports nothing was removed.
        self.assertFalse(self.store.delete(digest))

    def test_iter_digests_lists_everything(self) -> None:
        digests = {self.store.put(f"obj-{i}".encode()) for i in range(20)}
        self.assertEqual(set(self.store.iter_digests()), digests)


if __name__ == "__main__":
    unittest.main()
