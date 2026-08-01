# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Changed
- Renamed the program from flac2mp3 to AcoustID: `flac2mp3.py` is now
  `acoustid.py`, the window title and the `QSettings` org/app identifiers
  changed to "AcoustID" (previously saved settings under the old
  "flac2mp3" identifier are not carried over), and the conversion log
  prefix changed from `flac2mp3-` to `acoustid-convert-`.

### Added
- New **Import to Lidarr** button (optional, off by default, entirely
  independent of everything else in the window): hands the current folder
  to Lidarr's own Manual Import API instead of writing to Lidarr's
  database directly or requiring its import UI. New `lidarr.py` module
  wraps the API (best-effort rescan so Lidarr's database catches up with
  files this tool changed on disk, scan a folder for matches, submit the
  fully-matched ones, poll until the import command finishes); files
  Lidarr can't auto-match from embedded tags are left untouched and
  reported back together with Lidarr's own rejection reason (e.g.
  "already has file"), so a surprising "0 imported" is diagnosable
  instead of a dead end.
- New **Lidarr Settings...** dialog holding the Lidarr URL/API key (moved
  out of the main window), with a **Test Connection** button that
  verifies the host/port is reachable and the API key is accepted before
  you rely on them for an import.
- The last folder opened (via **Browse...** or on the command line) is
  now remembered between runs and reopened automatically on launch.
- New **Check AcoustID (incl. MP3)** button: rescans the folder for both
  `.flac` and `.mp3` files and runs the AcoustID check on all of them.
  Scoped strictly to that button - Start and Check AcoustID Only still
  only see `.flac` files, and this never converts or auto-corrects
  anything. `check_acoustid()` now reads the existing recording ID from
  either Vorbis comments (FLAC) or the ID3 UFID frame (MP3) so the
  match/mismatch comparison works on both.
- Optional **Auto-correct mismatched MBID** checkbox: when a Mismatch is
  found with a confident enough AcoustID score (>= 0.5), the FLAC's
  `musicbrainz_trackid` tag is rewritten to the correct recording before
  conversion. Off by default; never applies during a Check AcoustID Only
  run, which stays fully report-only.
- A **Mismatch** AcoustID result now spells out what the correct tag
  would be: both the currently tagged artist/title/MBID and AcoustID's
  suggested artist/title/MBID are included in the detail tooltip, instead
  of just "not found in AcoustID results."
- AcoustID lookups are now rate-limited to 4 requests/second, shared
  across all worker threads, so a high parallel-jobs setting can't exceed
  AcoustID's rate limit and get the client throttled or blocked.

### Fixed
- AcoustID errors (e.g. an invalid API key) showed a useless generic
  "400 Client Error" dump instead of the actual reason. AcoustID's JSON
  error body is now parsed before raising on HTTP status, so the real
  message (e.g. "invalid API key") is shown.

### Added
- Optional AcoustID check (off by default): fingerprints each FLAC with
  `fpcalc` and looks it up via the AcoustID/MusicBrainz web service,
  reporting Match/Mismatch/Identified/No match/Error per file in a new
  table column and in the log. Requires `fpcalc` on PATH and a free
  AcoustID API key entered in the UI. Report-only — never blocks or
  alters conversion.

## [0.4]

### Added
- Regression tests for the 0.3 fixes: a source file disappearing
  mid-batch, and a FLAC tagged with both `date` and `year`.

## [0.3]

### Fixed
- An unexpected error during a single file's conversion (e.g. the source
  file disappearing mid-batch) could crash the worker thread unhandled,
  leaving the UI stuck with Start/Cancel both disabled and no error
  shown. Such failures are now caught and reported like any other
  conversion failure, and the batch continues.
- FLAC files tagged with both `date` and `year` had one silently
  overwrite the other in the MP3's `TDRC` frame. `year` is no longer
  mapped onto `TDRC` and is preserved as its own `TXXX` frame instead.

## [0.2]

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
