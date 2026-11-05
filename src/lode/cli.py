"""Command-line interface for Lode.

Thin by design: parse arguments, open (or create) the repository, drive the
engine in :mod:`lode.repository`, format the result for humans or ``--json``, and
map domain errors to process exit codes. All real work lives in the engine so it
stays unit-testable without spawning a process.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .errors import LodeError
from .models import DIR, FILE, SYMLINK, Snapshot
from .repository import ADDED, DELETED, MODIFIED, Repository

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

_STATUS_LABEL = {ADDED: "added", DELETED: "deleted", MODIFIED: "modified"}


# -- formatting helpers --------------------------------------------------
def _human_size(num: int) -> str:
    size = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"  # pragma: no cover - unreachable


def _fmt_time(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


def _snapshot_summary(snap: Snapshot) -> dict:
    return {
        "id": snap.id,
        "short_id": snap.short_id,
        "parent": snap.parent,
        "message": snap.message,
        "created_at": snap.created_at,
        "stats": snap.stats.to_dict(),
    }


# -- repository access ---------------------------------------------------
def _cwd(args: argparse.Namespace) -> Path:
    return Path(args.directory).resolve() if args.directory else Path.cwd()


def _open(args: argparse.Namespace) -> Repository:
    return Repository.discover(_cwd(args))


# -- commands ------------------------------------------------------------
def _cmd_init(args: argparse.Namespace) -> int:
    target = (_cwd(args) / args.path).resolve()
    repo = Repository.init(target)
    print(f"Initialized empty Lode repository in {repo.lode_dir}")
    return EXIT_OK


def _cmd_save(args: argparse.Namespace) -> int:
    repo = _open(args)
    snap = repo.save(message=args.message, rehash=args.rehash)
    if args.json:
        print(json.dumps(_snapshot_summary(snap)))
        return EXIT_OK
    stats = snap.stats
    print(
        f"Saved snapshot {snap.short_id}  "
        f"{stats.files} files, {_human_size(stats.total_size)}  "
        f"({stats.new_chunks} new chunks, {_human_size(stats.new_bytes)} added)"
    )
    return EXIT_OK


def _cmd_list(args: argparse.Namespace) -> int:
    repo = _open(args)
    snaps = repo.snapshots()
    head = repo.head()
    if args.json:
        print(json.dumps([_snapshot_summary(s) for s in snaps]))
        return EXIT_OK
    if not snaps:
        print("No snapshots yet. Create one with 'lode save'.")
        return EXIT_OK
    for snap in reversed(snaps):
        marker = "*" if snap.id == head else " "
        message = snap.message or "(no message)"
        print(
            f"{marker} {snap.short_id}  {_fmt_time(snap.created_at)}  "
            f"{snap.stats.files:>5} files  {_human_size(snap.stats.total_size):>9}  {message}"
        )
    return EXIT_OK


def _cmd_show(args: argparse.Namespace) -> int:
    repo = _open(args)
    snap = repo.load_snapshot(repo.resolve(args.ref))
    if args.json:
        payload = _snapshot_summary(snap)
        if args.files:
            payload["entries"] = [e.to_dict() for e in snap.entries]
        print(json.dumps(payload))
        return EXIT_OK
    print(f"snapshot {snap.id}")
    if snap.parent:
        print(f"parent   {snap.parent[:12]}")
    print(f"date     {_fmt_time(snap.created_at)}")
    print(f"message  {snap.message or '(no message)'}")
    st = snap.stats
    print(
        f"stats    {st.files} files, {st.dirs} dirs, {st.symlinks} symlinks, "
        f"{_human_size(st.total_size)} ({st.chunks} chunks)"
    )
    if args.files:
        print()
        for entry in sorted(snap.entries, key=lambda e: e.path):
            glyph = {FILE: "f", DIR: "d", SYMLINK: "l"}.get(entry.type, "?")
            suffix = f" -> {entry.target}" if entry.type == SYMLINK else ""
            print(f"  {glyph} {entry.path}{suffix}")
    return EXIT_OK


def _render_changes(changes: list) -> None:
    for change in changes:
        print(f"  {change.status} {change.path}")


def _cmd_diff(args: argparse.Namespace) -> int:
    repo = _open(args)
    old = repo.load_snapshot(repo.resolve(args.old))
    new = repo.load_snapshot(repo.resolve(args.new))
    changes = repo.diff(old, new)
    if args.json:
        print(json.dumps([{"status": c.status, "path": c.path} for c in changes]))
        return EXIT_OK
    if not changes:
        print(f"No changes between {old.short_id} and {new.short_id}.")
        return EXIT_OK
    _render_changes(changes)
    added = sum(1 for c in changes if c.status == ADDED)
    deleted = sum(1 for c in changes if c.status == DELETED)
    modified = sum(1 for c in changes if c.status == MODIFIED)
    print(f"\n{added} added, {modified} modified, {deleted} deleted")
    return EXIT_OK


def _cmd_status(args: argparse.Namespace) -> int:
    repo = _open(args)
    changes = repo.status()
    if args.json:
        print(json.dumps([{"status": c.status, "path": c.path} for c in changes]))
        return EXIT_OK
    if not changes:
        head = repo.head()
        print("Working tree clean." if head else "Working tree clean (no snapshots yet).")
        return EXIT_OK
    _render_changes(changes)
    counts = {key: sum(1 for c in changes if c.status == key) for key in _STATUS_LABEL}
    print(
        f"\n{counts[ADDED]} added, {counts[MODIFIED]} modified, {counts[DELETED]} deleted "
        "(relative to HEAD)"
    )
    return EXIT_OK


def _cmd_restore(args: argparse.Namespace) -> int:
    repo = _open(args)
    snap = repo.load_snapshot(repo.resolve(args.ref))
    dest = (_cwd(args) / args.into).resolve()
    count = repo.restore(snap, dest, force=args.force)
    print(f"Restored {count} files from {snap.short_id} into {dest}")
    return EXIT_OK


def _cmd_forget(args: argparse.Namespace) -> int:
    repo = _open(args)
    snapshot_id = repo.resolve(args.ref)
    repo.forget(snapshot_id)
    print(f"Forgot snapshot {snapshot_id[:12]} (run 'lode prune' to reclaim space)")
    return EXIT_OK


def _cmd_prune(args: argparse.Namespace) -> int:
    repo = _open(args)
    removed, reclaimed = repo.prune()
    if args.json:
        print(json.dumps({"removed": removed, "reclaimed_bytes": reclaimed}))
        return EXIT_OK
    print(f"Pruned {removed} unreferenced objects, reclaimed {_human_size(reclaimed)}")
    return EXIT_OK


def _cmd_verify(args: argparse.Namespace) -> int:
    repo = _open(args)
    problems = repo.verify()
    if args.json:
        print(json.dumps([{"digest": d, "problem": p} for d, p in problems]))
    elif not problems:
        print("OK: all referenced objects are present and intact.")
    else:
        for digest, problem in problems:
            print(f"  {problem:<8} {digest[:12]}")
        print(f"\n{len(problems)} problem(s) found")
    return EXIT_OK if not problems else EXIT_ERROR


def _cmd_info(args: argparse.Namespace) -> int:
    repo = _open(args)
    data = repo.info()
    if args.json:
        print(json.dumps(data))
        return EXIT_OK
    print(f"root        {data['root']}")
    print(f"snapshots   {data['snapshots']}")
    print(f"objects     {data['objects']}")
    print(f"store size  {_human_size(data['physical_bytes'])}")
    print(f"logical     {_human_size(data['logical_bytes'])}")
    print(f"dedup ratio {data['dedup_ratio']:.2f}x")
    if data["head"]:
        print(f"head        {data['head'][:12]}")
    return EXIT_OK


# -- argument parser -----------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lode",
        description="Content-addressed snapshot backups for a single folder.",
    )
    parser.add_argument("--version", action="version", version=f"lode {__version__}")
    parser.add_argument(
        "-C",
        "--directory",
        metavar="DIR",
        help="run as if lode were started in DIR instead of the current directory",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_init = sub.add_parser("init", help="create a new repository")
    p_init.add_argument("path", nargs="?", default=".", help="directory to protect (default: .)")
    p_init.set_defaults(func=_cmd_init)

    p_save = sub.add_parser("save", help="record a snapshot of the working tree")
    p_save.add_argument("-m", "--message", default="", help="snapshot message")
    p_save.add_argument(
        "--rehash", action="store_true", help="re-read every file instead of trusting mtime/size"
    )
    p_save.add_argument("--json", action="store_true", help="emit the new snapshot as JSON")
    p_save.set_defaults(func=_cmd_save)

    p_list = sub.add_parser("list", aliases=["ls"], help="list snapshots, newest first")
    p_list.add_argument("--json", action="store_true", help="emit snapshots as JSON")
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="show a snapshot's metadata")
    p_show.add_argument("ref", nargs="?", default="HEAD", help="snapshot reference (default: HEAD)")
    p_show.add_argument("--files", action="store_true", help="also list every path in the snapshot")
    p_show.add_argument("--json", action="store_true", help="emit as JSON")
    p_show.set_defaults(func=_cmd_show)

    p_diff = sub.add_parser("diff", help="show changes between two snapshots")
    p_diff.add_argument("old", help="base snapshot reference")
    p_diff.add_argument("new", nargs="?", default="HEAD", help="target reference (default: HEAD)")
    p_diff.add_argument("--json", action="store_true", help="emit changes as JSON")
    p_diff.set_defaults(func=_cmd_diff)

    p_status = sub.add_parser(
        "status", aliases=["st"], help="show working-tree changes relative to HEAD"
    )
    p_status.add_argument("--json", action="store_true", help="emit changes as JSON")
    p_status.set_defaults(func=_cmd_status)

    p_restore = sub.add_parser("restore", help="restore a snapshot into a directory")
    p_restore.add_argument("ref", nargs="?", default="HEAD", help="snapshot reference")
    p_restore.add_argument("--into", required=True, metavar="DIR", help="destination directory")
    p_restore.add_argument(
        "--force", action="store_true", help="write even if the destination is not empty"
    )
    p_restore.set_defaults(func=_cmd_restore)

    p_forget = sub.add_parser("forget", help="remove a snapshot (keeps the objects)")
    p_forget.add_argument("ref", help="snapshot reference")
    p_forget.set_defaults(func=_cmd_forget)

    p_prune = sub.add_parser("prune", aliases=["gc"], help="delete objects no snapshot references")
    p_prune.add_argument("--json", action="store_true", help="emit a summary as JSON")
    p_prune.set_defaults(func=_cmd_prune)

    p_verify = sub.add_parser("verify", help="check that every referenced object is intact")
    p_verify.add_argument("--json", action="store_true", help="emit problems as JSON")
    p_verify.set_defaults(func=_cmd_verify)

    p_info = sub.add_parser("info", help="show repository-wide statistics")
    p_info.add_argument("--json", action="store_true", help="emit statistics as JSON")
    p_info.set_defaults(func=_cmd_info)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return args.func(args)
    except LodeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
