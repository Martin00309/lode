# Roadmap

Lode's 0.1 release is a complete, correct core: content-defined chunking,
deduplicated content-addressed storage, snapshots with history, restore, GC, and
integrity verification. This document sketches where it could go next. Nothing
here is a commitment — it is a record of intent and an invitation to discuss.

## Near term

- **Streaming large files.** Today a file is read fully into memory to be
  chunked. Chunk from a streaming reader so arbitrarily large files use bounded
  memory.
- **A persistent save index.** Cache the previous scan (path → size/mtime/chunks)
  so repeated saves of large trees skip even the directory walk where possible.
- **`lode restore --file PATH`.** Restore a single path out of a snapshot rather
  than the whole tree.
- **Retention policies.** `lode forget --keep-last N` / `--keep-within 30d` to
  prune history by policy instead of one id at a time.

## Medium term

- **Packed objects.** Combine many small chunks into pack files to cut
  per-object filesystem overhead for trees with many tiny files.
- **Repository-at-a-distance.** Allow the `.lode` store to live outside the
  protected tree (`--repo DIR`), so one store can back up several sources.
- **Richer diffs.** Byte-level or hunk-level diffs for text files, and rename
  detection in `diff`/`status`.

## Longer term

- **Encryption at rest.** Per-repository keys with authenticated encryption of
  objects and manifests.
- **Remote stores.** Pluggable backends (SFTP, S3-compatible) behind the same
  object-store interface.
- **Concurrency.** Safe parallel chunking/hashing on save for large trees.

## Non-goals

- Becoming a general-purpose version-control system. Lode snapshots a folder;
  it does not branch, merge, or track line-level authorship.
- A daemon or background scheduler. Lode is a command you run (by hand or from
  cron); it does not want to be a resident service.
