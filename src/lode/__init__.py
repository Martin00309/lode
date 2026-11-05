"""Lode — a content-addressed snapshot backup tool for a single folder.

Lode protects one directory tree. ``lode init`` creates a ``.lode`` repository
inside it; ``lode save`` records a point-in-time snapshot. Files are split into
content-defined chunks (FastCDC) and stored once by SHA-256 digest, so repeated
snapshots only pay for the bytes that actually changed.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
