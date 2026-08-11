# Plan: Go port of the headless/CLI surface (sidecut-go)

## Problem

The project is Python (PySide6 GUI + `core.py`/`config.py`/`lidarr.py`
headless logic). Want a Go version too, without touching or breaking the
existing Python project.

## Goal

A new, independent `sidecut-go/` module living alongside the Python code
in this repo, covering the **headless/CLI surface only**:

- FLAC -> MP3 conversion (shells out to `ffmpeg`, same as `core.py`)
- Tag reading/writing (FLAC read, MP3/ID3 write)
- AcoustID fingerprint lookup + MusicBrainz metadata check
- Lidarr REST client (manual import, queue, connection test)
- Headless config file (`config.ini`, script-dir-first with
  `~/.config/flac2mp3/` fallback, env var overrides) - same precedence
  rules as `config.py`
- `--configure` interactive text UI - same behavior as `configure_cli.py`
- Lidarr Custom Script hook mode (`lidarr_*` env vars) - same behavior as
  `lidarr_hook.py`

**Out of scope for this plan**: the PySide6 GUI (`sidecut.py`'s
`MainWindow`/`LidarrSettingsDialog`/etc.), `library_stats.py`'s GUI
charts. Go has no good Qt equivalent - a GUI port is a separate decision
to make later, once the headless core exists and works standalone as a
Go CLI tool.

The Python project (`flac2mp3`) is not modified by this work, other than
this plan document and (optionally) a top-level README pointer to the Go
version once it exists.

## Design

### Layout

```
flac2mp3/                  (existing Python project, untouched)
sidecut-go/                (new)
  go.mod
  cmd/sidecut/main.go      CLI entrypoint, flag parsing, dispatch
  internal/config/         config.go equivalent
  internal/core/           core.py equivalent (convert, tag read/write, AcoustID/MusicBrainz)
  internal/lidarr/         lidarr.py equivalent (REST client)
  internal/configureui/    configure_cli.py equivalent
  internal/hook/           lidarr_hook.py equivalent (Custom Script env-var mode)
```

Package boundaries mirror the existing Python module boundaries 1:1, so
the Python source stays the reference/spec during porting - behavior
questions get resolved by reading the matching `.py` file and its tests.

### Key library decisions (resolved by the spike)

- **FLAC reading**: hand-rolled `internal/flactag` package, not a
  library. `dhowden/tag` was tried first and rejected: it collapses
  repeated Vorbis comment keys to the last value only (`map[string]string`)
  and keeps only the last embedded picture (`m.p`, overwritten on every
  PICTURE block) - both are silent data loss vs mutagen, which
  `core.py`'s `copy_tags` depends on (multi-value GENRE, multiple cover
  images). The FLAC metadata block format is simple enough that a ~250
  line hand-rolled reader (STREAMINFO for duration, VORBIS_COMMENT and
  every PICTURE block, preserving order/multiplicity) was less risk than
  a lossy dependency. Verified against a real sample file: duration
  matched `ffprobe` exactly, multi-value GENRE preserved as two separate
  entries, picture read correctly.
- **ID3 writing**: `github.com/bogem/id3v2/v2` - confirmed via spike.
  Supports `SetTitle`/`SetArtist`/`SetAlbum`, `AddUserDefinedTextFrame`
  (TXXX), `AddUFIDFrame`, `AddAttachedPicture` (APIC), and `Open()` binds
  a `*Tag` straight to a file path for `Save()`. One gotcha worth noting
  for the real port: TXXX frames must have a unique `Description` per the
  ID3v2 spec, so a repeated Vorbis comment key must be joined with `"; "`
  into a single frame before writing - same rule `core.py`'s `copy_tags`
  already follows (`joined = "; ".join(values)`), just verify the port
  does the join *before* calling `AddUserDefinedTextFrame`, not after
  (writing one frame per value silently drops all but the last, since
  they share the same TXXX identity key).
  Spike test: `sidecut-go/internal/id3spike/spike_test.go` - full
  ffmpeg-convert -> flactag-read -> id3v2-write -> id3v2-read-back round
  trip against a real sample file, passing. Doubles as a first
  integration test once `internal/core` exists for real.
- **HTTP**: standard `net/http` is enough for both AcoustID/MusicBrainz
  and Lidarr's REST API - no extra dependency needed.
- **ffmpeg invocation**: `os/exec`, same subprocess approach as
  `core.py`'s `run_ffmpeg`.
- **ini config file**: hand-roll a minimal parser/writer matching
  `config.py`'s exact format rather than pull in `go-ini/ini` - small
  enough, and avoids a dependency for something this simple (same
  reasoning as `internal/flactag`).

### Config precedence (must match config.py exactly)

Highest wins: env vars > config.ini next to the `sidecut` binary (if it
exists) > config.ini in `~/.config/flac2mp3/` (or `$XDG_CONFIG_HOME`) >
built-in empty default. First-run save target defaults to next to the
binary if that directory is writable, else `~/.config/flac2mp3/` -
mirrors `config.py`'s `resolve_config_path()`.

### Testing

Go's standard `testing` package, one `_test.go` per package, porting
the same test cases that exist in `tests/test_config.py`,
`tests/test_configure_cli.py`, `tests/test_lidarr.py`,
`tests/test_lidarr_hook.py` - not just "add some tests" but a checklist
of "every Python test case has a Go equivalent" so behavior parity is
verifiable, not just assumed.

## Implementation Progress

- [x] Spike: pick FLAC/ID3 read+write libraries, prove round-trip tag
      read/write works on a sample file - hand-rolled `internal/flactag`
      reader + `bogem/id3v2` writer, verified against a real sample
- [x] `sidecut-go/go.mod` + directory scaffold
- [x] `internal/config`: ini read/write, `ResolveConfigPath()`, env var
      overrides, precedence order - port `tests/test_config.py` cases
- [x] `internal/configureui`: `--configure` TUI - port
      `tests/test_configure_cli.py` cases (incl. Lidarr live-check
      verification step before save). Lidarr check is injected as a
      `LidarrChecker` func rather than importing `internal/lidarr`
      directly, so this package doesn't have to wait on that one -
      `cmd/sidecut` wires the real implementation in later.
- [x] `internal/core` (partial): ffmpeg conversion (`ConvertOne`/
      `RunFFmpeg`), `FindFLACFiles`/`FindFLACAndMP3Files`, `CopyTags`
      (full Picard-compatible frame mapping table, ported from
      `core.py`'s `_STANDARD_FRAME_BUILDERS`/`_FREETEXT_DESCRIPTIONS`) -
      ported the `tests/test_core.py` cases covering these. **Not yet
      ported**: `apply_release_type`/`apply_release_provenance`/
      `correct_acoustid_mismatch` - they rewrite a FLAC's own Vorbis
      comments in place, which needs a FLAC metadata *writer*
      (`internal/flactag` is read-only by design, see the spike notes
      above). `check_acoustid`/AcoustID+MusicBrainz HTTP lookups are now
      ported, in `internal/acoustid` (see below) rather than `internal/core`.
- [x] `internal/acoustid`: fingerprint via `fpcalc -json`, AcoustID lookup
      (`meta=recordings`/`meta=releasegroups`, two requests - the API only
      honors one meta mode per call), direct MusicBrainz recording lookup
      for release date/originaldate, and the match/mismatch/identified/
      no_match/error comparison against the file's existing
      `musicbrainz_trackid` (FLAC Vorbis comment or MP3 UFID frame) -
      ported the `tests/test_core.py` `check_acoustid` cases (match,
      mismatch with/without a linked recording, identified, no_match,
      release-type/date/originaldate surfacing incl. preferring the
      release matching a tagged album, provenance keyed off the tagged
      recording rather than the first linked one, reading tags from an
      already-converted MP3, API error message surfaced instead of a
      generic status error, rate limiter shared across calls). Every
      dependency (fpcalc, HTTP client, AcoustID/MusicBrainz URLs) is
      injectable on `Checker` so tests hit `httptest.Server` instead of
      the real network/binary. `Checker` is wired into `cmd/sidecut`: a
      `sidecut check <file-or-folder>` command, plus an automatic
      post-conversion check printed after each file in `sidecut convert`
      when `acoustid_api_key` is configured and `fpcalc` is on PATH
      (silently skipped, with a one-time warning, if the key is set but
      `fpcalc` isn't found). Not wired into the Lidarr Custom Script hook
      mode (`internal/hook`) - `lidarr_hook.py` itself never called
      `check_acoustid` either, only `sidecut.py`'s GUI worker did.
      `ApplyReleaseType`/`ApplyReleaseProvenance`/`CorrectAcoustIDMismatch`
      are now ported too (`internal/acoustid/apply.go`), on top of
      `internal/flactag`'s new writer (see below) - ported the matching
      `tests/test_core.py` cases (writes missing tag, never overwrites an
      existing one, fills only the missing field of a pair, skips a
      low-confidence or non-mismatch or recording-id-less correction,
      `CorrectAcoustIDMismatch` never touches an MP3). **Not yet wired
      into `cmd/sidecut`**: `sidecut check`/`convert` only report a
      `Check`, they don't call these three to act on it - that's an
      opt-in decision (autocorrect rewriting a tag someone might disagree
      with) worth its own flag design, not bundled into this pass.
- [x] `internal/flactag` **write support**: `SetComments` rewrites a
      FLAC's VORBIS_COMMENT block - replacing every value for a given key
      (case-insensitively) with one new value, appended after the
      surviving comments - while copying every other metadata block
      (STREAMINFO, PICTUREs, unknown block types) and the audio data
      through byte-for-byte unchanged. Always does a full-file rewrite to
      a temp file + atomic rename, rather than mutagen's in-place-padding
      trick - simpler and still correct, just marginally more I/O.
      Mirrors mutagen's `FLAC(path)[key] = [value]; tags.save()`, which
      is all `apply_release_type`/`apply_release_provenance`/
      `correct_acoustid_mismatch` ever do to a FLAC.
- [x] `internal/lidarr` (partial): retry/backoff (`withRetry`),
      `CheckConnection`, `RemapPathToLidarr`/`LidarrPathToLocal`,
      `DeleteTrackfile`, `GetQueue` - ported the matching
      `tests/test_lidarr.py` cases (all against `httptest.Server`, no
      real network). **Not yet ported**: the manual-import workflow
      (`get_manual_import_candidates`/`submit_manual_import`/
      `import_folder`/`force_reimport_folder`, stale-trackfile cleanup)
      - same retry+`http.Client` pattern already established here, just
      a lot of surface area; deferred for scope.
- [x] `internal/hook`: Lidarr Custom Script env-var mode - ported the
      `tests/test_lidarr_hook.py` cases that don't depend on
      `lidarr.import_folder` (which isn't ported - see above). When
      Lidarr URL/API key *are* configured, `RunFromEnvironment` still
      converts every added FLAC but prints that it can't queue the
      reimport step yet, rather than silently no-op'ing. No
      QSettings-equivalent `quality` setting exists in this headless-only
      port - callers pass a fixed `"v0"` today.
- [x] `cmd/sidecut`: CLI entrypoint wiring `--configure` (via
      `configureui.Run(lidarr.CheckConnection)`), hook-mode detection
      (`hook.IsInvocation()`), and `sidecut convert <folder> [quality]`.
      Manually smoke-tested end to end against a real sample FLAC file -
      converts, tags land correctly, matches the Python GUI's Transcode
      output.
- [x] Configurable `ffmpeg` path - not a port of any Python behavior
      (`core.py` always looks on `PATH`, no override), added because a
      headless box may keep `ffmpeg` outside `PATH` (container mount,
      static build next to the binary). New `ffmpeg_path` config
      field/`FFMPEG_PATH` env var, following the same file/env
      precedence as `acoustid_api_key`/`lidarr_*` - blank means "look up
      `ffmpeg` on PATH" (the old, only, behavior), same as before. A
      package-level `core.FFmpegPath` var (default `"ffmpeg"`) is what
      `CheckFFmpeg`/`RunFFmpeg` actually invoke; `cmd/sidecut`'s `main()`
      sets it from config before dispatching to any subcommand (covers
      `convert`, `check`, and the Lidarr hook mode, since hook mode calls
      straight into `core.ConvertOne` too).
- [x] `sidecut-go/README.md`: build/install instructions, what's ported
      vs not (no GUI yet, no AcoustID check, no Lidarr reimport), how it
      relates to the Python project
- [x] Manual test: cross-compiled `CGO_ENABLED=0 GOOS=linux GOARCH=amd64`
      (static - the default cgo-linked build depends on glibc, risky
      across distros/versions; static sidesteps that), copied to the
      same unraid box as the Python version. Ran `--configure` against
      the box's real, already-existing `config.ini` (written by the
      Python version) - read and masked all three fields correctly,
      confirming file-format compatibility; declined the save prompt so
      nothing was overwritten. Full conversion untested there: that
      unraid box's bare shell has no `ffmpeg` on `PATH` (the Python
      version likely runs it via Docker/Nerd Tools) - conversion itself
      is covered by `internal/core`'s local tests instead.

## Open questions

- Repo layout: keep `sidecut-go/` in this same repo (chosen) vs a
  separate repo - revisit if the Go version grows large enough that
  shared CI/release tooling becomes awkward.
- Should the Go `config.ini` be wire-compatible enough that both
  Python and Go versions can point at the exact same file on a shared
  Unraid box? (Leaning yes - same format, same precedence rules, so
  this is mostly "don't diverge" rather than new work.)
- GUI: revisit once the headless core is solid - Fyne/Gio/Wails/skip
  entirely are all still on the table.
