---
name: release
description: Cut a new release of flac2mp3 - bump version, update CHANGELOG.md, tag, and push. Use when the user asks to cut/make a release or bump the version.
---

Every notable change gets a bullet under `## [Unreleased]` in `CHANGELOG.md` as
part of the same commit that makes the change (`### Added`/`### Changed`/
`### Fixed`, following Keep a Changelog style) - don't batch this up for
release time.

To cut a release:

1. Bump `__version__` in `sidecut.py`.
2. In `CHANGELOG.md`, rename the `## [Unreleased]` heading to `## [X.Y]` and
   add a fresh empty `## [Unreleased]` above it.
3. Commit both as `Bump version to X.Y`.
4. Tag: `git tag -a sidecut-vX.Y -m "..."` (annotated, message summarizes the
   version's changes), then plain `git push && git push origin sidecut-vX.Y`.
   As of 2026-08-05 this directory is a standalone repo with its own `.git`
   and origin github.com/drachenhort/sidecut - no subtree push, no monorepo
   root. (Before that it was a subdirectory of a `/home/sigma/git` monorepo
   and every push had to go through `git subtree push --prefix=flac2mp3`;
   that history was split out and the old monorepo git dir retired, so the
   subtree step no longer applies.)
   Releases through v0.14 are tagged `acoustid-vX.Y` (pre-rename) - leave
   those as-is, only new tags use the `sidecut-` prefix.
5. Create the GitHub release from that same CHANGELOG section - don't
   hand-write separate release notes, the changelog entry already is them:
   ```bash
   .claude/skills/release/extract-changelog-section.sh X.Y > /tmp/release-notes-X.Y.md
   gh release create sidecut-vX.Y --title "Sidecut vX.Y" --notes-file /tmp/release-notes-X.Y.md
   ```
