"""End-to-end tests driving the CLI through ``main`` against a temp repository."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Tuple

from lode.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "work"
        self.root.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, *args: str) -> Tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["-C", str(self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def write(self, relpath: str, content: bytes) -> None:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    # -- lifecycle -------------------------------------------------------
    def test_init_save_list_flow(self) -> None:
        code, out, _ = self.run_cli("init")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Initialized", out)

        self.write("a.txt", b"hello\n")
        code, out, _ = self.run_cli("save", "-m", "first")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Saved snapshot", out)

        code, out, _ = self.run_cli("list")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("first", out)

    def test_save_json_emits_snapshot_id(self) -> None:
        self.run_cli("init")
        self.write("a.txt", b"hello\n")
        code, out, _ = self.run_cli("save", "-m", "msg", "--json")
        self.assertEqual(code, EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(len(payload["id"]), 64)
        self.assertEqual(payload["message"], "msg")

    def test_list_empty_repository(self) -> None:
        self.run_cli("init")
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("No snapshots", out)

    # -- status & diff ---------------------------------------------------
    def test_status_before_and_after_save(self) -> None:
        self.run_cli("init")
        self.write("a.txt", b"hello\n")
        _code, out, _ = self.run_cli("status")
        self.assertIn("a.txt", out)
        self.run_cli("save", "-m", "first")
        _code, out, _ = self.run_cli("status")
        self.assertIn("clean", out.lower())

    def test_diff_between_snapshots(self) -> None:
        self.run_cli("init")
        self.write("a.txt", b"one\n")
        self.run_cli("save", "-m", "first")
        self.write("b.txt", b"two\n")
        self.run_cli("save", "-m", "second")
        code, out, _ = self.run_cli("diff", "HEAD~1", "HEAD", "--json")
        self.assertEqual(code, EXIT_OK)
        changes = {(c["status"], c["path"]) for c in json.loads(out)}
        self.assertIn(("A", "b.txt"), changes)

    # -- restore ---------------------------------------------------------
    def test_restore_writes_files(self) -> None:
        self.run_cli("init")
        self.write("a.txt", b"hello\n")
        self.write("dir/b.txt", b"world\n")
        self.run_cli("save", "-m", "first")
        dest = Path(self._tmp.name) / "restored"
        code, out, _ = self.run_cli("restore", "HEAD", "--into", str(dest))
        self.assertEqual(code, EXIT_OK)
        self.assertEqual((dest / "a.txt").read_bytes(), b"hello\n")
        self.assertEqual((dest / "dir" / "b.txt").read_bytes(), b"world\n")

    # -- verify & info ---------------------------------------------------
    def test_verify_ok(self) -> None:
        self.run_cli("init")
        self.write("a.txt", b"hello\n")
        self.run_cli("save", "-m", "first")
        code, out, _ = self.run_cli("verify")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("OK", out)

    def test_verify_reports_corruption_with_error_exit(self) -> None:
        self.run_cli("init")
        self.write("a.txt", b"x" * 50_000)
        self.run_cli("save", "-m", "first")
        objects = Path(self.root) / ".lode" / "objects"
        target = next(p for p in objects.rglob("*") if p.is_file())
        target.write_bytes(b"garbage")
        code, out, _ = self.run_cli("verify")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("problem", out)

    def test_info_json(self) -> None:
        self.run_cli("init")
        self.write("a.txt", b"hello\n")
        self.run_cli("save", "-m", "first")
        code, out, _ = self.run_cli("info", "--json")
        self.assertEqual(code, EXIT_OK)
        data = json.loads(out)
        self.assertEqual(data["snapshots"], 1)

    # -- forget & prune --------------------------------------------------
    def test_forget_then_prune(self) -> None:
        self.run_cli("init")
        self.write("a.txt", b"hello\n")
        self.run_cli("save", "-m", "first")
        self.write("b.txt", os.urandom(20_000))
        _code, out, _ = self.run_cli("save", "-m", "second", "--json")
        head_id = json.loads(out)["id"]
        code, _out, _ = self.run_cli("forget", head_id)
        self.assertEqual(code, EXIT_OK)
        code, out, _ = self.run_cli("prune", "--json")
        self.assertEqual(code, EXIT_OK)
        self.assertGreater(json.loads(out)["removed"], 0)

    # -- error handling --------------------------------------------------
    def test_no_command_prints_usage(self) -> None:
        code, _out, _ = self.run_cli()
        self.assertEqual(code, EXIT_USAGE)

    def test_command_outside_repository_errors(self) -> None:
        code, _out, err = self.run_cli("list")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("error:", err)

    def test_unknown_reference_errors(self) -> None:
        self.run_cli("init")
        self.write("a.txt", b"hello\n")
        self.run_cli("save", "-m", "first")
        code, _out, err = self.run_cli("show", "nonexistent")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()
