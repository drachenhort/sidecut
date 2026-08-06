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
- [ ] `internal/core`: ffmpeg conversion, tag read/write, AcoustID
      lookup, MusicBrainz check - port `tests/test_core.py` cases
- [ ] `internal/lidarr`: REST client (manual import, queue, connection
      test, retry/backoff) - port `tests/test_lidarr.py` cases
- [ ] `internal/hook`: Lidarr Custom Script env-var mode - port
      `tests/test_lidarr_hook.py` cases
- [ ] `cmd/sidecut`: CLI entrypoint wiring `--configure`, hook-mode
      detection, and a `convert <folder>` command (headless equivalent
      of the GUI's Transcode button)
- [ ] `sidecut-go/README.md`: build/install instructions, what's ported
      vs not (no GUI yet), how it relates to the Python project
- [ ] Manual test: cross-compile for Linux/amd64, run on the same
      unraid box as the Python version, confirm config.ini
      compatibility (same file format/location rules)

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
