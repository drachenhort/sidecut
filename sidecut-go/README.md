# sidecut-go

Go port of [flac2mp3](..)'s headless/CLI surface. Lives alongside the
original Python project (`sidecut.py` etc. at the repo root) rather than
replacing it - see `docs/plans/go-port-headless.md` for the plan this is
tracking, including what's ported, what's deliberately deferred, and why.

## What this is

- FLAC -> MP3 conversion via `ffmpeg`, with full tag copying (standard
  ID3 frames, MusicBrainz/AcoustID TXXX frames, cover art) matching the
  same Picard-compatible mapping the Python version uses.
- A headless `config.ini` (script-dir-first, falling back to
  `~/.config/flac2mp3/`, env vars override both) and an interactive
  `--configure` text UI to set it - same file format and precedence
  rules as the Python version, so both can point at the same file on a
  shared box.
- Lidarr connection testing and a Lidarr Custom Script hook mode
  (reads `lidarr_*` env vars, same as the Python `lidarr_hook.py`).

## What this isn't (yet)

No GUI - that's what the Python project's `sidecut.py` is for. Also not
yet ported: the AcoustID/MusicBrainz fingerprint check, writing
release-type/date tags back onto a FLAC, and handing converted files off
to Lidarr's Manual Import API (the hook mode converts files but can't
queue the reimport step yet). See the plan doc for why each of these was
scoped out rather than rushed.

## Build

```bash
go build -o sidecut ./cmd/sidecut
```

## Usage

```bash
./sidecut convert /path/to/music/folder [v0|v2|cbr320]   # default: v0
./sidecut --configure                                     # set API keys
```

Registered as a Lidarr Custom Script (`Settings -> Connect -> +
-> Custom Script`, path pointing at this binary), it's invoked
automatically via `lidarr_*` environment variables - no flags needed for
that path, same as the Python version's Custom Script mode.

## Test

```bash
go test ./...
```

Needs `ffmpeg` on `PATH` for the tests that actually convert audio
(`internal/core`, `internal/hook`) - those skip themselves if it's
missing.
