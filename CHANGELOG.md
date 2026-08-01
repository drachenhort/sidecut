# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Changed
- Tag mapping now matches MusicBrainz Picard's own ID3v2.3 output:
  many previously-generic-TXXX fields (isrc, conductor, remixer,
  lyricist, sort fields, original release info, website, license,
  etc.) are written as their proper standard ID3 frames, MusicBrainz
  and AcoustID identifiers use Picard's exact TXXX descriptions (e.g.
  `TXXX:Acoustid Id`), and the recording MBID (`MUSICBRAINZ_TRACKID`)
  is now written as a `UFID` frame instead of `TXXX`, matching what
  Picard itself writes and expects when re-reading the file.

### Fixed
- If the log file can't be written to the scanned folder (e.g. a
  read-only NFS share), the conversion thread crashed silently and the
  UI got stuck with Start/Cancel both disabled. It now falls back to
  `~/.local/share/flac2mp3/logs/`, and if that also fails, shows a
  "Conversion failed" dialog and re-enables Start.

## [0.1]

### Fixed
- `batch_finished` signal used a 32-bit `int` for total source/destination
  byte counts, causing an `OverflowError` on batches larger than ~2.1GB.
  The byte-count parameters are now `qint64`.

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
