# Contributing to Lode

Thanks for taking an interest. Lode aims to stay small, dependency-free, and
easy to read, so contributions are held to that bar.

## Getting set up

```bash
git clone https://github.com/Martin00309/lode
cd lode
python -m pip install -e .
```

Lode has no runtime dependencies. The only development tool is
[Ruff](https://docs.astral.sh/ruff/) for linting and formatting.

## Running the checks

```bash
python -m unittest discover -s tests -v   # the full test suite
ruff check .                               # lint
ruff format --check .                      # formatting
```

The test suite uses only the standard library's `unittest`, and every test runs
against a temporary directory, so it is safe to run anywhere.

## Conventions

- **Zero runtime dependencies.** The standard library is the whole toolbox. A PR
  that adds a third-party runtime dependency will be turned down unless it is
  genuinely unavoidable.
- **Behavior lives in the engine.** `lode/cli.py` only parses arguments and
  formats output; the real work belongs in `repository.py`, `store.py`,
  `chunker.py`, and friends, where it can be unit-tested without a subprocess.
- **On-disk formats are a contract.** Changing the object or manifest layout
  means bumping the format version in `config.json` and keeping older
  repositories readable. Think twice before touching how ids are computed.
- **Test behavior, not internals.** Prefer tests that save real trees and assert
  on round-trips, diffs, and GC outcomes over tests that poke at private state.
- **Keep the surface honest.** If a change narrows or widens what a command
  guarantees, update the README table and `CHANGELOG.md` in the same PR.

## Commit messages

Write imperative, present-tense subjects ("Add prune reachability check"), and
explain the *why* in the body when it is not obvious from the diff.
