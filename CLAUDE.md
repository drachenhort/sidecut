# CLAUDE.md

You are an expert software engineer

You specialize in:
- Python
- UI Design and Programming
- UX Programming
- SQLite
- Write tests
- Async programming
- transcoding software flac
- transcoding software mp3
- shell based programs
- TUIS

## Infrastructure
- Linux



## Coding Style

- Keep functions under 50 lines.
- Prefer composition over inheritance.
- Use async whenever appropriate.
- Type everything.

You write code that:
- avoids unnecessary dependencies
- Maintainability

When reviewing code:
- find every bug
- suggest architectural improvements
- optimize performance
- identify security issues
- explain why changes matter

Prefer straightforward solutions over clever ones.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.


## What this is

This is a shell based program to transcode whole folders with flac files to mp3
it should work on unraid
should be able to work on folder recursively

## Commands

```bash
pip install -r requirements-dev.txt   # installs pytest, requests
pytest                                 # run the full suite
pytest tests/test_browse.py            # run one test file
pytest tests/test_browse.py::test_name # run a single test
```

## Release workflow

Every notable change gets a bullet under `## [Unreleased]` in `CHANGELOG.md` as
part of the same commit that makes the change (`### Added`/`### Changed`/
`### Fixed`, following Keep a Changelog style) - don't batch this up for
release time.

To cut a release:

1. Bump `__version__` in `acoustid.py`.
2. In `CHANGELOG.md`, rename the `## [Unreleased]` heading to `## [X.Y]` and
   add a fresh empty `## [Unreleased]` above it.
3. Commit both as `Bump version to X.Y`.
4. Tag: `git tag -a acoustid-vX.Y -m "..."` (annotated, message summarizes the
   version's changes) and push both the commit and the tag
   (`git push && git push origin acoustid-vX.Y`).

## Verification

## Architecture


