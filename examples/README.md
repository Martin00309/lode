# Examples

A short, copy-pasteable walkthrough of a full Lode workflow. Everything happens
inside a throwaway directory, so it is safe to run as-is.

## A five-minute tour

```bash
# 1. Make a project with some content.
mkdir demo && cd demo
echo "hello world"            > notes.txt
mkdir src
echo "print('v1')"           > src/app.py
head -c 100000 /dev/urandom  > src/data.bin

# 2. Keep noise out of snapshots.
cat > .lodeignore <<'EOF'
*.log
build/
EOF
mkdir build && echo junk > build/out.o
echo noise > debug.log

# 3. Initialize and take the first snapshot.
lode init
lode save -m "initial import"

# 4. Make some changes and see them before saving.
echo "print('v2')" > src/app.py      # modify
echo "TODO: write docs" > TODO.md    # add
rm notes.txt                         # delete
lode status

# 5. Snapshot again — only the delta is stored.
lode save -m "second pass"
lode list
```

`lode status` prints the pending changes (sorted by path):

```text
  A TODO.md
  D notes.txt
  M src/app.py

1 added, 1 modified, 1 deleted (relative to HEAD)
```

And `lode list` shows both snapshots, newest first (the `.lodeignore` file is
itself tracked, so the count is four):

```text
* 7f8e9d0c1b2a  2026-11-05 14:07:55      4 files    97.7 KiB  second pass
  a1b2c3d4e5f6  2026-11-05 14:02:10      4 files    97.7 KiB  initial import
```

Notice the second `save` barely grew the store — only a few bytes: `src/data.bin`
was untouched, so its chunks were reused and only the edited `src/app.py` and the
new `TODO.md` cost any space.

## Going back in time

```bash
# Compare the two snapshots.
lode diff HEAD~1 HEAD

# Recover the original tree into a fresh directory.
lode restore HEAD~1 --into ./recovered
cat ./recovered/notes.txt      # "hello world" is back

# Inspect a snapshot's contents without restoring it.
lode show HEAD --files
```

## Housekeeping

```bash
lode info                  # store size, object count, dedup ratio
lode verify                # confirm every referenced object is intact
lode forget HEAD           # drop the newest snapshot...
lode prune                 # ...then reclaim the space it uniquely held
```

The `.lodeignore` file meant `debug.log` and `build/out.o` never entered any
snapshot — confirm with `lode show HEAD --files`.
