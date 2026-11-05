# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-05

Initial release.

### Added

- Content-defined chunking (FastCDC with normalized chunking) for shift-resistant
  deduplication.
- Content-addressed object store with zlib compression, sharded layout, atomic
  writes, and on-read integrity verification.
- `lode init` / `save` / `status` / `list` / `show` / `diff` / `restore` /
  `forget` / `prune` / `verify` / `info` commands.
- Snapshot references: `HEAD`, short-id prefixes, and `HEAD~N` ancestry.
- `.lodeignore` support (a practical `.gitignore` subset).
- Incremental saves via a size/mtime/mode fast path, with `--rehash` to force a
  full re-read.
- Preservation of file modes, empty directories, and symlinks across a
  save/restore round-trip.
- `--json` output for scriptable commands.

[Unreleased]: https://github.com/Martin00309/lode/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Martin00309/lode/releases/tag/v0.1.0
