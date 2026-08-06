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
      ported**: `check_acoustid`/AcoustID+MusicBrainz HTTP lookups, and
      `apply_release_type`/`apply_release_provenance`/
      `correct_acoustid_mismatch` - the latter three rewrite a FLAC's
      own Vorbis comments in place, which needs a FLAC metadata
      *writer* (`internal/flactag` is read-only by design, see the
      spike notes above); the AcoustID/MusicBrainz HTTP logic itself is
      straightforward but sizeable (`net/http` + JSON, no writer
      needed) and was deferred for scope, not for a technical reason -
      pick either back up as its own pass.
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
