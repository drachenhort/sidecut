# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Changed
- Replaced the terminal UI (curses dashboard + folder browser) with a
  native PySide6/Qt window, matching `radiotop`'s stack and following the
  system's KDE Plasma/Breeze theme automatically. Conversion logic moved
  into a framework-agnostic `core.py`, unit tested independently of the UI.
- The window shows a native KDE folder picker, quality/parallel-jobs
  controls, and a per-file table with a live progress bar (percent, encode
  speed) fed from ffmpeg's `-progress` stream; Cancel stops queued files
  and lets in-flight ones finish or abort.

### Added
- Embedded FLAC cover art (front/back/etc.) is now copied to the MP3 as
  ID3 `APIC` frames, alongside the existing tag preservation.
- Full tag preservation from FLAC to MP3: well-known tags are mapped to
  standard ID3v2 frames, and every other tag (AcoustID, MusicBrainz,
  ReplayGain, and any other custom Vorbis comment) is kept as a `TXXX`
  frame so nothing is silently dropped.
- Safety: the original `.flac` is deleted only after the `.mp3` is
  verified (non-zero size, ffmpeg exit code 0, tags copied successfully);
  failures are logged and the source file is left untouched.
- Re-running on a partially converted folder is safe/resumable.
- `pytest` test suite (`tests/test_core.py`) covering recursive scanning,
  tag and cover-art preservation, progress reporting, cancellation, and
  failure handling.

### Removed
- The `curses`/`asyncio`-based terminal UI and its CLI flags (`--yes`,
  `--plain`, `--workers` as CLI args, folder-path positional argument
  semantics) in favor of the GUI. `flac2mp3.py <folder>` still works as a
  shortcut to pre-fill the folder field.
