"""Content-defined chunking (FastCDC).

Deduplication only works if a file is cut into pieces at boundaries that depend
on the *content* around them rather than on absolute byte offsets. If we chopped
files every fixed N bytes, inserting a single byte near the front would shift
every later boundary and invalidate every downstream chunk. Content-defined
chunking solves this: a boundary is declared wherever a rolling fingerprint of
the preceding bytes hits a target pattern, so an edit only disturbs the chunks it
actually touches and the rest keep their digests (and stay deduplicated).

This module implements FastCDC with *normalized chunking*: two cut masks are used
either side of the average size so the resulting chunk-length distribution is
tighter around the mean than classic Rabin chunking. The "gear" hash is a single
table lookup and shift per byte, which keeps the inner loop fast even in pure
Python.

The gear table is derived deterministically from a fixed seed, so chunk
boundaries are stable across machines, processes, and Python versions — a hard
requirement for a store that is addressed by content hash.
"""

from __future__ import annotations

from typing import Iterator

__all__ = [
    "MIN_SIZE",
    "AVG_SIZE",
    "MAX_SIZE",
    "Chunk",
    "chunk",
    "chunk_boundaries",
]

_MASK64 = (1 << 64) - 1

# Default chunk-size envelope. The average governs how aggressively data is
# split: smaller averages dedupe finer-grained edits at the cost of more objects
# and more per-chunk overhead. 8 KiB is a good general-purpose default.
MIN_SIZE = 2 * 1024
AVG_SIZE = 8 * 1024
MAX_SIZE = 64 * 1024

# How far the two normalized masks sit from the average, in bits. A larger value
# makes chunk sizes cluster harder around the average. Level 2 is FastCDC's
# recommended default.
_NORMALIZATION = 2


def _build_gear_table(seed: int) -> tuple:
    """Return 256 pseudo-random 64-bit values, one per possible byte value.

    Generated with splitmix64 — a tiny, well-distributed, fully deterministic
    PRNG — so the table (and therefore every chunk boundary) is reproducible
    without shipping a 2 KB literal or depending on ``random`` seeding.
    """

    values = []
    state = seed & _MASK64
    for _ in range(256):
        state = (state + 0x9E3779B97F4A7C15) & _MASK64
        z = state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
        z = z ^ (z >> 31)
        values.append(z & _MASK64)
    return tuple(values)


_GEAR = _build_gear_table(0x2545F4914F6CDD1D)


class Chunk:
    """One content-defined slice of a byte string.

    ``offset`` is the chunk's start within the original buffer and ``data`` are
    its bytes; ``len(chunk)`` is its length.
    """

    __slots__ = ("offset", "data")

    def __init__(self, offset: int, data: bytes):
        self.offset = offset
        self.data = data

    def __len__(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Chunk(offset={self.offset}, size={len(self.data)})"


def _masks(avg_size: int) -> "tuple[int, int]":
    """Return the (strict, relaxed) cut masks for an average chunk size.

    ``bits`` is log2 of the average, so a random fingerprint satisfies
    ``fp & mask == 0`` roughly once every ``2**bits`` bytes. The strict mask
    (more bits) is used before the average is reached to discourage short
    chunks; the relaxed mask (fewer bits) is used after it to discourage long
    ones. Together they pull the size distribution toward the average.
    """

    bits = max(1, (avg_size - 1).bit_length())
    strict_bits = min(63, bits + _NORMALIZATION)
    relaxed_bits = max(1, bits - _NORMALIZATION)
    return (1 << strict_bits) - 1, (1 << relaxed_bits) - 1


def _next_cut(
    data: bytes,
    start: int,
    min_size: int,
    avg_size: int,
    max_size: int,
    mask_strict: int,
    mask_relaxed: int,
) -> int:
    """Return the end offset of the chunk beginning at ``start``.

    The gear fingerprint is advanced one byte at a time. Cut points are only
    considered once the chunk has reached ``min_size``; the strict mask applies
    until ``avg_size`` and the relaxed mask after it. If no boundary is found the
    chunk is force-cut at ``max_size`` (or end of data).
    """

    n = len(data)
    limit = min(start + max_size, n)
    # Too little data left to bother hashing: emit the remainder as one chunk.
    if limit - start <= min_size:
        return limit

    normal = min(start + avg_size, limit)
    fingerprint = 0
    i = start
    min_end = start + min_size
    while i < limit:
        fingerprint = ((fingerprint << 1) + _GEAR[data[i]]) & _MASK64
        i += 1
        if i < min_end:
            continue
        mask = mask_strict if i < normal else mask_relaxed
        if (fingerprint & mask) == 0:
            return i
    return limit


def chunk_boundaries(
    data: bytes,
    *,
    min_size: int = MIN_SIZE,
    avg_size: int = AVG_SIZE,
    max_size: int = MAX_SIZE,
) -> Iterator[int]:
    """Yield the end offset of each chunk, in order, ending with ``len(data)``.

    An empty input yields nothing. ``min_size <= avg_size <= max_size`` must
    hold; otherwise a :class:`ValueError` is raised.
    """

    if not (0 < min_size <= avg_size <= max_size):
        raise ValueError("chunk sizes must satisfy 0 < min <= avg <= max")

    mask_strict, mask_relaxed = _masks(avg_size)
    start = 0
    n = len(data)
    while start < n:
        end = _next_cut(data, start, min_size, avg_size, max_size, mask_strict, mask_relaxed)
        yield end
        start = end


def chunk(
    data: bytes,
    *,
    min_size: int = MIN_SIZE,
    avg_size: int = AVG_SIZE,
    max_size: int = MAX_SIZE,
) -> Iterator[Chunk]:
    """Split ``data`` into content-defined :class:`Chunk` objects.

    The concatenation of every chunk's ``data`` reproduces the input exactly.
    """

    start = 0
    for end in chunk_boundaries(data, min_size=min_size, avg_size=avg_size, max_size=max_size):
        yield Chunk(start, data[start:end])
        start = end
