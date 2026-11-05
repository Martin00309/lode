"""Tests for ``.lodeignore`` pattern matching."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lode.ignore import IgnoreMatcher, load_ignore_file


def _matcher(*patterns: str) -> IgnoreMatcher:
    return IgnoreMatcher(list(patterns))


class IgnoreMatcherTests(unittest.TestCase):
    def test_wildcard_matches_at_any_depth(self) -> None:
        m = _matcher("*.log")
        self.assertTrue(m.is_ignored("debug.log"))
        self.assertTrue(m.is_ignored("logs/debug.log"))
        self.assertTrue(m.is_ignored("a/b/c.log"))
        self.assertFalse(m.is_ignored("debug.txt"))

    def test_question_mark_matches_single_char(self) -> None:
        m = _matcher("file?.txt")
        self.assertTrue(m.is_ignored("file1.txt"))
        self.assertFalse(m.is_ignored("file10.txt"))

    def test_character_class(self) -> None:
        m = _matcher("*.[oa]")
        self.assertTrue(m.is_ignored("main.o"))
        self.assertTrue(m.is_ignored("lib.a"))
        self.assertFalse(m.is_ignored("main.c"))

    def test_dir_only_pattern(self) -> None:
        m = _matcher("build/")
        self.assertTrue(m.is_ignored("build", is_dir=True))
        self.assertTrue(m.is_ignored("build/out.o"))  # inside an excluded dir
        self.assertFalse(m.is_ignored("build", is_dir=False))  # a file named build

    def test_anchored_pattern(self) -> None:
        m = _matcher("/config")
        self.assertTrue(m.is_ignored("config"))
        self.assertFalse(m.is_ignored("src/config"))

    def test_unanchored_pattern(self) -> None:
        m = _matcher("config")
        self.assertTrue(m.is_ignored("config"))
        self.assertTrue(m.is_ignored("src/config"))

    def test_middle_slash_anchors(self) -> None:
        m = _matcher("src/build")
        self.assertTrue(m.is_ignored("src/build"))
        self.assertFalse(m.is_ignored("lib/src/build"))

    def test_double_star(self) -> None:
        m = _matcher("a/**/b")
        self.assertTrue(m.is_ignored("a/b"))
        self.assertTrue(m.is_ignored("a/x/b"))
        self.assertTrue(m.is_ignored("a/x/y/b"))
        self.assertFalse(m.is_ignored("a/x/c"))

    def test_negation_reincludes(self) -> None:
        m = _matcher("*.log", "!keep.log")
        self.assertTrue(m.is_ignored("other.log"))
        self.assertFalse(m.is_ignored("keep.log"))

    def test_cannot_reinclude_under_excluded_dir(self) -> None:
        m = _matcher("build/", "!build/keep.txt")
        # Git semantics: a parent directory exclusion cannot be undone.
        self.assertTrue(m.is_ignored("build/keep.txt"))

    def test_comments_and_blank_lines_ignored(self) -> None:
        m = _matcher("# a comment", "", "   ", "*.tmp")
        self.assertEqual(len(m.rules), 1)
        self.assertTrue(m.is_ignored("scratch.tmp"))

    def test_empty_matcher_ignores_nothing(self) -> None:
        self.assertFalse(_matcher().is_ignored("anything"))


class LoadIgnoreFileTests(unittest.TestCase):
    def test_missing_file_gives_empty_matcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            matcher = load_ignore_file(Path(tmp) / ".lodeignore")
            self.assertFalse(matcher.is_ignored("x"))

    def test_loads_patterns_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".lodeignore"
            path.write_text("# ignore caches\n__pycache__/\n*.pyc\n", encoding="utf-8")
            matcher = load_ignore_file(path)
            self.assertTrue(matcher.is_ignored("__pycache__", is_dir=True))
            self.assertTrue(matcher.is_ignored("pkg/mod.pyc"))
            self.assertFalse(matcher.is_ignored("pkg/mod.py"))


if __name__ == "__main__":
    unittest.main()
