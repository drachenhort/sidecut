---
name: release
description: Cut a new release of flac2mp3 - bump version, update CHANGELOG.md, tag, and push. Use when the user asks to cut/make a release or bump the version.
---

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
