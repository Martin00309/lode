# Lode

A content-addressed snapshot backup tool for a single folder. Think of it as a
tiny "time machine" you can drop into any directory: `lode save` records a
point-in-time snapshot, and because files are split into content-defined chunks
and stored once by hash, repeated saves only cost the bytes that actually
changed.

Lode is a single, zero-dependency Python package. The entire store — objects,
snapshots, and history — lives in a `.lode` directory inside the folder it
protects, so a backup is self-contained and easy to reason about.

```text
$ lode init
Initialized empty Lode repository in /home/me/project/.lode

$ lode save -m "before the big refactor"
Saved snapshot a1b2c3d4e5f6  142 files, 3.1 MiB  (138 new chunks, 3.1 MiB added)

$ lode save -m "after the refactor"
Saved snapshot 7f8e9d0c1b2a  142 files, 3.1 MiB  (9 new chunks, 41.2 KiB added)

$ lode list
* 7f8e9d0c1b2a  2026-11-05 14:07:55    142 files    3.1 MiB  after the refactor
  a1b2c3d4e5f6  2026-11-05 14:02:10    142 files    3.1 MiB  before the big refactor

$ lode diff HEAD~1 HEAD
  M src/engine.py
  A src/engine_v2.py
  D src/legacy.py

$ lode restore a1b2c3 --into ./recovered
Restored 142 files from a1b2c3d4e5f6 into /home/me/project/recovered
```

## Why content-addressed?

Most naive backup tools either copy the whole tree every time (wasteful) or diff
whole files (fragile). Lode splits every file into **content-defined chunks**
using [FastCDC](https://www.usenix.org/conference/atc16/technical-sessions/presentation/xia):
chunk boundaries are chosen by a rolling fingerprint of the surrounding bytes,
not by fixed offsets. That one decision buys three things:

- **Deduplication.** Identical chunks — within a file, across files, across
  snapshots — are stored exactly once, keyed by their SHA-256 digest.
- **Shift resistance.** Inserting or deleting bytes near the start of a file
  only re-chunks the affected region; every downstream chunk keeps its digest
  and stays deduplicated. (A fixed-size chunker would re-write the whole file.)
- **Integrity.** Every object is addressed by the hash of its own contents, so
  `lode verify` can detect silent corruption, and a restore can never hand you
  the wrong bytes without noticing.

## Install

Lode targets Python 3.9+ and has no runtime dependencies.

```bash
git clone https://github.com/Martin00309/lode
cd lode
python -m pip install -e .
```

This puts a `lode` command on your path. You can also run it as a module without
installing: `python -m lode`.

## Usage

Lode behaves a lot like a miniature version-control system, but for backups.

| Command | What it does |
| --- | --- |
| `lode init [PATH]` | Create a `.lode` repository in `PATH` (default: current dir). |
| `lode save [-m MSG]` | Snapshot the working tree and advance `HEAD`. |
| `lode status` | Show what changed in the working tree since `HEAD`. |
| `lode list` | List snapshots, newest first (`*` marks `HEAD`). |
| `lode show [REF] [--files]` | Show a snapshot's metadata (and optionally its file list). |
| `lode diff OLD [NEW]` | Show added / modified / deleted paths between two snapshots. |
| `lode restore [REF] --into DIR` | Materialize a snapshot into a directory. |
| `lode forget REF` | Remove a snapshot (keeps the objects until you prune). |
| `lode prune` | Garbage-collect objects no snapshot references. |
| `lode verify` | Check that every referenced object is present and intact. |
| `lode info` | Show repository-wide statistics and the dedup ratio. |

Most commands accept `--json` for scripting.

### References

Anywhere a command takes a snapshot reference you can use:

- `HEAD` (or `@`) — the most recent snapshot.
- A full id, or any unambiguous prefix (e.g. `a1b2c3`).
- `HEAD~N` or `<id>~N` — walk back `N` snapshots along the parent chain.

### Ignoring files

Create a `.lodeignore` file at the repository root. It uses a practical subset
of `.gitignore` syntax:

```gitignore
# caches and build output
__pycache__/
*.pyc
build/
dist/

# but keep this one log
*.log
!important.log
```

The repository's own `.lode` directory is always excluded.

## How it works

```text
project/
  .lode/
    config.json          # format version + chunk parameters
    HEAD                 # id of the current snapshot
    objects/ab/cdef...   # zlib-compressed, content-addressed chunks
    snapshots/<id>.json  # one manifest per snapshot
```

- **`save`** walks the tree (honoring `.lodeignore`), chunks each file, writes
  any new chunks to `objects/`, and records a manifest listing every path with
  its chunk digests, mode, size, and mtime. Unchanged files are detected by
  matching size + mtime + mode against the parent snapshot, so their chunks are
  reused without re-reading the file (`save --rehash` forces a full re-read).
- **`restore`** reads a manifest and reassembles each file by concatenating its
  chunks, recreating directories, symlinks, and permissions.
- **`prune`** performs a mark-and-sweep: it collects every chunk referenced by
  any surviving snapshot and deletes the rest.

A snapshot id is the SHA-256 of its own canonical contents (parent, message,
timestamp, and file tree), so identical trees still receive distinct, stable
ids.

## Limits (and the roadmap)

Lode is young and deliberately small. Today it loads each file fully into memory
to chunk it, keeps one unencrypted store on the local filesystem, and treats the
working tree as single-writer. Streaming large files, encryption, and remote
stores are on the [roadmap](ROADMAP.md).

## Development

```bash
python -m unittest discover -s tests -v   # run the test suite
ruff check . && ruff format --check .      # lint and format
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for conventions.

## License

[MIT](LICENSE)
