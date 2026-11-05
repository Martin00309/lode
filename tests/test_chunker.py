"""Tests for content-defined chunking.

The properties under test are the ones deduplication actually depends on:
boundaries are deterministic, chunks reconstruct the input exactly, sizes stay
within the configured envelope, and — crucially — inserting bytes near the start
of a buffer only disturbs nearby chunks instead of shifting every boundary.
"""

from __future__ import annotations

import hashlib
import random
import unittest

from lode.chunker import AVG_SIZE, MAX_SIZE, MIN_SIZE, chunk, chunk_boundaries


def _random_bytes(n: int, seed: int) -> bytes:
    return random.Random(seed).randbytes(n)


def _digests(data: bytes) -> list:
    return [hashlib.sha256(c.data).hexdigest() for c in chunk(data)]


class ChunkerTests(unittest.TestCase):
    def test_empty_input_yields_nothing(self) -> None:
        self.assertEqual(list(chunk(b"")), [])
        self.assertEqual(list(chunk_boundaries(b"")), [])

    def test_small_input_is_a_single_chunk(self) -> None:
        data = b"short"
        chunks = list(chunk(data))
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].data, data)
        self.assertEqual(chunks[0].offset, 0)

    def test_reconstruction_is_exact(self) -> None:
        data = _random_bytes(250_000, seed=1)
        rebuilt = b"".join(c.data for c in chunk(data))
        self.assertEqual(rebuilt, data)

    def test_offsets_are_contiguous(self) -> None:
        data = _random_bytes(100_000, seed=2)
        cursor = 0
        for piece in chunk(data):
            self.assertEqual(piece.offset, cursor)
            cursor += len(piece)
        self.assertEqual(cursor, len(data))

    def test_boundaries_are_deterministic(self) -> None:
        data = _random_bytes(300_000, seed=3)
        self.assertEqual(list(chunk_boundaries(data)), list(chunk_boundaries(data)))

    def test_chunk_sizes_respect_the_envelope(self) -> None:
        data = _random_bytes(500_000, seed=4)
        chunks = list(chunk(data))
        self.assertGreater(len(chunks), 1)
        for piece in chunks:
            self.assertLessEqual(len(piece), MAX_SIZE)
        # Every chunk but the trailing remainder is at least the minimum size.
        for piece in chunks[:-1]:
            self.assertGreaterEqual(len(piece), MIN_SIZE)

    def test_average_size_is_in_a_sane_range(self) -> None:
        data = _random_bytes(1_000_000, seed=5)
        chunks = list(chunk(data))
        average = len(data) / len(chunks)
        # Normalized chunking should keep the mean reasonably near the target.
        self.assertGreater(average, AVG_SIZE / 2)
        self.assertLess(average, AVG_SIZE * 2)

    def test_insertion_only_disturbs_local_chunks(self) -> None:
        # The dedup-defining property: insert a few bytes near the front and most
        # downstream chunk digests must survive unchanged.
        original = _random_bytes(400_000, seed=6)
        mutated = original[:1000] + b"INJECTED-BYTES" + original[1000:]

        before = _digests(original)
        after = _digests(mutated)
        shared = set(before) & set(after)

        # A fixed-size chunker would share almost nothing after a 1-byte shift.
        self.assertGreater(len(shared), 0.8 * len(before))

    def test_appending_preserves_leading_chunks(self) -> None:
        original = _random_bytes(200_000, seed=7)
        extended = original + _random_bytes(50_000, seed=8)
        before = _digests(original)
        after = _digests(extended)
        # Everything before the append point should be identical.
        self.assertEqual(before[:-1], after[: len(before) - 1])

    def test_invalid_size_envelope_raises(self) -> None:
        with self.assertRaises(ValueError):
            list(chunk_boundaries(b"data", min_size=10, avg_size=5, max_size=100))
        with self.assertRaises(ValueError):
            list(chunk_boundaries(b"data", min_size=0, avg_size=5, max_size=10))


if __name__ == "__main__":
    unittest.main()
