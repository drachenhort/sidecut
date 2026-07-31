# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added
- Live curses dashboard (`radiotop`-style) showing per-worker current file,
  percent complete, elapsed time, and encode speed, parsed from ffmpeg's
  `-progress` stream, plus a scrolling list of recently finished files.
  Falls back automatically to the plain progress line when stdout isn't a
  TTY, or when `--plain` is passed.
- Initial version of `flac2mp3.py`: recursively scans a folder for `.flac`
  files and transcodes them to MP3 in place via `ffmpeg`.
- Simple interactive text UI (folder path, quality preset, confirmation)
  plus non-interactive CLI flags (`--quality`, `--yes`, `--workers`) for
  scripted/unattended runs (e.g. on Unraid).
- Parallel conversion via `asyncio`, with a live progress line.
- Full tag preservation from FLAC to MP3: well-known tags are mapped to
  standard ID3v2 frames, and every other tag (AcoustID, MusicBrainz,
  ReplayGain, and any other custom Vorbis comment) is kept as a `TXXX`
  frame so nothing is silently dropped.
- Safety: the original `.flac` is deleted only after the `.mp3` is
  verified (non-zero size, ffmpeg exit code 0, tags copied successfully);
  failures are logged and the source file is left untouched.
- Re-running the script on a partially converted folder is safe/resumable.
- `pytest` test suite covering recursive scanning, tag preservation,
  failure handling, and idempotent re-runs.
