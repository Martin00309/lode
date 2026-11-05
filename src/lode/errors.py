"""Exception hierarchy for Lode.

Every error the engine raises on purpose derives from :class:`LodeError`, so the
CLI can catch that single base, print a clean message, and exit non-zero without
leaking a traceback. Programming bugs (``ValueError`` and friends) are allowed to
propagate so they surface loudly during development.
"""

from __future__ import annotations


class LodeError(Exception):
    """Base class for all expected, user-facing Lode errors."""


class RepositoryError(LodeError):
    """A repository is missing, already present, or otherwise unusable."""


class NotARepository(RepositoryError):
    """No ``.lode`` repository was found at or above the working directory."""


class AlreadyInitialized(RepositoryError):
    """``lode init`` was run where a repository already exists."""


class SnapshotError(LodeError):
    """A snapshot could not be resolved, read, or written."""


class SnapshotNotFound(SnapshotError):
    """A snapshot reference did not match any stored snapshot."""


class AmbiguousSnapshot(SnapshotError):
    """A short snapshot id matched more than one snapshot."""

    def __init__(self, ref: str, matches: list):
        self.ref = ref
        self.matches = list(matches)
        preview = ", ".join(short[:12] for short in self.matches[:5])
        suffix = ", ..." if len(self.matches) > 5 else ""
        super().__init__(f"snapshot id '{ref}' is ambiguous ({preview}{suffix})")


class ObjectStoreError(LodeError):
    """The content-addressed object store is missing data or is corrupt."""


class CorruptObject(ObjectStoreError):
    """A stored object failed its content-hash check on read."""

    def __init__(self, digest: str):
        self.digest = digest
        super().__init__(f"object {digest[:12]} is corrupt (content hash mismatch)")


class MissingObject(ObjectStoreError):
    """A snapshot references a chunk that is not present in the store."""

    def __init__(self, digest: str):
        self.digest = digest
        super().__init__(f"object {digest[:12]} is missing from the store")


class RestoreError(LodeError):
    """A restore could not complete (e.g. the destination is not empty)."""
