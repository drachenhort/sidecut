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
   version's changes) and push both the commit and the tag. The commit
   must go via `git subtree push --prefix=flac2mp3 origin master` run from
   the monorepo root (`/home/sigma/git`), **not** plain `git push` - this
   repo's origin (github.com/drachenhort/flac2mp3) is a flattened mirror
   (flac2mp3/* at repo root, no monorepo prefix, no `.claude/settings.json`
   clutter); a plain `git push` sends the full monorepo-nested tree and
   reintroduces exactly that mess (this happened once already - see the
   2026-08-03 branch/history cleanup). Then `git push origin acoustid-vX.Y`
   for the tag as normal (tags are standalone refs, unaffected by this).
5. Create the GitHub release from that same CHANGELOG section - don't
   hand-write separate release notes, the changelog entry already is them:
   ```bash
   .claude/skills/release/extract-changelog-section.sh X.Y > /tmp/release-notes-X.Y.md
   gh release create acoustid-vX.Y --title "AcoustID vX.Y" --notes-file /tmp/release-notes-X.Y.md
   ```
