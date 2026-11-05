"""``.lodeignore`` pattern matching.

A ``.lodeignore`` file at the repository root lets you keep noise — build
output, virtual environments, caches — out of snapshots. The syntax is a
practical subset of ``.gitignore``:

* Blank lines and lines starting with ``#`` are ignored. A leading ``#`` or ``!``
  can be escaped with a backslash.
* ``*`` matches any run of characters except ``/``; ``?`` matches one such
  character; ``[abc]`` / ``[!abc]`` are character classes.
* ``**`` matches across directory separators (e.g. ``a/**/b``).
* A trailing ``/`` restricts a pattern to directories.
* A leading ``/`` — or any ``/`` in the middle — anchors the pattern to the
  repository root. A pattern with no such separator matches at any depth.
* A leading ``!`` negates an earlier pattern, re-including a match.

As in Git, the last matching pattern wins, and a file cannot be re-included if
one of its parent directories is excluded. The repository's own ``.lode``
directory is always excluded and cannot be overridden.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional

__all__ = ["IgnoreRule", "IgnoreMatcher", "load_ignore_file"]

IGNORE_FILENAME = ".lodeignore"


def _translate(pattern: str, anchored: bool) -> str:
    """Translate a glob ``pattern`` into a full-match regex string."""

    out: List[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                if i + 2 < n and pattern[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] in ("!", "^"):
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape(c))
                i += 1
            else:
                body = pattern[i + 1 : j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body + "]")
                i = j + 1
        elif c == "/":
            out.append("/")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    prefix = "^" if anchored else "(?:^|.*/)"
    return prefix + "".join(out) + r"\Z"


class IgnoreRule:
    """A single compiled ``.lodeignore`` pattern."""

    __slots__ = ("pattern", "negated", "dir_only", "_regex")

    def __init__(self, pattern: str):
        self.pattern = pattern
        text = pattern
        self.negated = text.startswith("!")
        if self.negated:
            text = text[1:]
        elif text.startswith("\\#") or text.startswith("\\!"):
            text = text[1:]
        self.dir_only = text.endswith("/")
        if self.dir_only:
            text = text[:-1]
        anchored = text.startswith("/") or ("/" in text.rstrip("/"))
        text = text.lstrip("/")
        self._regex = re.compile(_translate(text, anchored))

    def matches(self, path: str, is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        return self._regex.match(path) is not None


class IgnoreMatcher:
    """An ordered set of :class:`IgnoreRule` objects applied to a path."""

    def __init__(self, patterns: Optional[List[str]] = None):
        self.rules: List[IgnoreRule] = []
        for raw in patterns or []:
            self.add(raw)

    def add(self, raw: str) -> None:
        line = raw.rstrip("\n").rstrip("\r")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return
        # A trailing space is literal only when backslash-escaped; otherwise trim.
        if not line.endswith("\\ "):
            line = line.rstrip()
        self.rules.append(IgnoreRule(line))

    def _decide(self, path: str, is_dir: bool) -> Optional[bool]:
        """Return True to ignore, False to force-include, or None if unmatched."""

        decision: Optional[bool] = None
        for rule in self.rules:
            if rule.matches(path, is_dir):
                decision = not rule.negated
        return decision

    def is_ignored(self, relpath: str, is_dir: bool = False) -> bool:
        """Return whether ``relpath`` (POSIX, repo-relative) should be excluded.

        A path is excluded if it matches an active ignore rule, or if any of its
        parent directories is excluded — the latter cannot be undone by a
        negation on the path itself, mirroring Git's behaviour.
        """

        relpath = relpath.replace(os.sep, "/").strip("/")
        if not relpath:
            return False
        parts = relpath.split("/")
        for depth in range(len(parts) - 1):
            ancestor = "/".join(parts[: depth + 1])
            if self._decide(ancestor, True) is True:
                return True
        return self._decide(relpath, is_dir) is True


def load_ignore_file(path: Path) -> IgnoreMatcher:
    """Build a matcher from a ``.lodeignore`` file (empty if it is absent)."""

    matcher = IgnoreMatcher()
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return matcher
    for line in text.splitlines():
        matcher.add(line)
    return matcher
