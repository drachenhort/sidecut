# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Changed
- Renamed the program from AcoustID to **Sidecut** (window title, entry
  point `acoustid.py` → `sidecut.py`) - "AcoustID" is already the name of
  the external fingerprinting service this tool talks to, which made the
  program's own name confusing. All references to the actual AcoustID
  service/API/checkbox/column are unchanged, only the program's own
  identity moved.

## [0.14]

### Changed
- "Auto-continue after conversion" (Settings...) is now configured in
  seconds instead of minutes, for finer control - same 1800s (30 min)
  default, 0 still disables it.

## [0.13]

### Added
- Lidarr import's finished popup and skip-reasons log now group skipped
  files by normalized reason (e.g. "32x Has missing tracks") instead of
  just a flat file list, so a big batch's dominant failure mode is
  visible at a glance instead of scrolling through every filename.

## [0.12]

### Added
- Lidarr import skip reasons now add a hint when Lidarr's rejection is
  "Has missing tracks" - this usually means Lidarr matched the album to
  the wrong release edition (e.g. a folder mixing original and remix
  versions of the same titles doesn't line up with whichever single
  MusicBrainz release Lidarr picked), not a metadata profile block.
- The "Conversion complete" popup now auto-continues after a configurable
  countdown (Settings... > "Auto-continue after conversion", default 30
  minutes, 0 disables it) instead of always blocking on an OK click -
  useful for unattended/scheduled runs, since auto-import to Lidarr
  couldn't start until someone dismissed the popup.

## [0.11]

### Added
- The Lidarr Queue window now also shows the live status of manual
  imports this tool itself queues (Import to Lidarr / Force Reimport),
  as extra rows alongside the download queue - previously those only
  showed up in the separate import log window, and never in the Queue
  window, since Lidarr's `/api/v1/queue` only covers downloads, not
  manual-import commands. A finished command's row is shown once, then
  drops off on the next refresh.
- Closing the main window now asks "Are you sure you want to quit?"
  first (defaults to No) instead of quitting immediately.

### Changed
- Settings dialog is bigger by default (was sized to content, cramped
  the path-hint text).
- AcoustID API key field in Settings is plain text again instead of
  masked - it's a personal API key, not a password, and masking it just
  meant an extra hold-to-reveal click to double-check what was typed.

## [0.10]

### Added
- The Lidarr Queue window can now be reopened after closing it (new
  "Queue..." button next to Lidarr's Settings...), instead of staying
  closed until app restart. Closing just hides it and pauses polling -
  reopening resumes polling in place, so the last known queue state is
  never lost.
- Lidarr Queue window's columns (Title, Status, Quality, Progress, Time
  left) are now sortable by clicking their headers; Progress sorts
  numerically instead of alphabetically.

## [0.9]

### Changed
- Lidarr import's "couldn't auto-match" file list is now written to a
  timestamped log file next to the imported folder instead of being
  dumped into the finished-import popup, which could grow huge with
  many unmatched files.

## [0.8]

### Fixed
- `convert_one` no longer deletes a fully converted, correctly tagged MP3
  just because removing the source FLAC afterwards failed (e.g. a
  transient NFS/permission error) - the conversion is now reported as
  successful and the leftover source is logged as a warning instead.
- `run_ffmpeg` cancellation no longer hangs forever if ffmpeg ignores
  SIGTERM (e.g. stuck on a stalled network mount): it now escalates to
  `SIGKILL` after a 5-second grace period.
- The AcoustID batch worker's shared log file is now written through a
  lock, so log lines from concurrently converting files can no longer
  interleave mid-write.
- `lidarr.get_queue`, `_queue_command`, and `_to_import_file` now raise
  `LidarrError` (instead of a bare `KeyError`/`TypeError`) on an
  unexpected/malformed Lidarr API response, so `lidarr_hook.py`'s
  single `except LidarrError` handler catches them too instead of
  crashing the headless import.

## [0.7]

### Added
- New **Lidarr Queue** window: when a Lidarr URL/API key are already
  configured, it auto-opens on startup and shows Lidarr's live download
  queue (title, status, quality, progress, time left), polling
  `GET /api/v1/queue` every 5 seconds so you can watch downloads progress
  without switching to Lidarr's own UI. It's read-only, and closing it
  stops polling for the rest of the session - there's no reopen control,
  by design, since re-adding one would mean tracking window lifecycle
  state that isn't worth the complexity for a convenience view.
- New **Force Reimport...** button next to Import to Lidarr: unlike a plain
  import (which always skips files Lidarr already has a track file record
  for), this reimports everything, including already-tracked files - useful
  after correcting tags on a file Lidarr previously matched wrong. Lidarr's
  only way to drop a TrackFile record (`DELETE /api/v1/trackfile`) always
  deletes the underlying file too, so nothing is ever deleted here: each
  already-tracked file is moved aside to a temporary holding folder first,
  which lets Lidarr's existing existence-checked stale-trackfile cleanup
  safely drop just the database record, then the file is moved straight
  back before the normal reimport runs (`lidarr.force_reimport_folder`).
  Before anything happens, a dry-run preview dialog
  (`lidarr.plan_force_reimport`) lists exactly which files are in scope and
  flags this path as new and not battle-tested - nothing runs until
  Proceed is clicked.
- Collection Summary now also shows a second chart classifying each release
  as Original, Reissue, or Compilation, using the `releasetype`
  compilation secondary type and a `date`/`originaldate` mismatch
  (`library_stats.scan_release_provenance`).
- AcoustID check/fill can now tag `date`/`originaldate` itself, no Picard
  required: `check_acoustid` queries MusicBrainz's web service directly
  (no API key needed) off the matched recording's MBID for the release's
  own date and its release-group's original release date, and "Fill
  release type" now also backfills these via the new
  `apply_release_provenance` (`core.py`).
- New **Sort Reissues/Compilations...** button: scans the chosen folder
  (read-only) for releases classified as reissues or compilations and
  previews moving each into a "Reissues"/"Compilations" subfolder of its
  own artist folder (e.g. "Simple Minds/Album (1998 Remaster)" -> "Simple
  Minds/Reissues/Album (1998 Remaster)", "Simple Minds/Greatest Hits" ->
  "Simple Minds/Compilations/Greatest Hits") - keeps an artist folder down
  to its original studio albums, with every remaster and best-of/comp
  release tucked out of the way instead. Each release has a per-row "Sort
  into .../Keep as it is" toggle, and nothing moves until the list is
  reviewed and confirmed (`library_stats.plan_declutter_moves`/
  `execute_declutter_moves`).

### Fixed
- Multi-disc releases ripped as "Album/CD 01", "Album/CD 02", ... were
  being treated as one release per disc, so Sort Reissues/Compilations
  would scatter a single box set across several nested "Reissues"/
  "Compilations" moves instead of relocating the whole album folder.
  `library_stats._iter_releases` now collapses a directory whose
  immediate subfolders are all disc-named into one release.
- `run_ffmpeg` could hang forever converting a file whose ffmpeg run
  produces enough stderr output to fill the OS pipe buffer: only stdout
  (the `-progress` stream) was being read while the conversion ran, so
  ffmpeg would block writing stderr and never produce more progress
  either. stderr is now drained concurrently on its own thread.

## [0.6]

### Added
- App/window icon (`icons/acoustid.svg`, rasterized to several PNG sizes).
- **Lidarr Settings...** is now just **Settings...** and also holds the
  AcoustID API key (previously a separate field in the main window). The
  key field is masked like a password; a 👁 button next to it reveals the
  real value only while the mouse is held over it, re-masking the moment
  it leaves - no click-to-toggle state to forget to turn back off.

### Changed
- The **Start** button is now **Transcode**, to read clearly alongside
  Check AcoustID Only/+MP3 now that the AcoustID check is documented as a
  standalone use of this tool, not just a pre-conversion step.

## [0.5]

### Added
- **Auto-correct mismatched MBID** now also backfills a missing
  `releasetype` tag (Album/EP/Single/Compilation/...) from AcoustID/
  MusicBrainz - the tag the Collection Summary reads, so previously
  untagged releases stop showing up as `Unknown`. Additive-only (never
  overwrites an existing tag), works on both FLAC and already-converted
  MP3, and - unlike the MBID rewrite - runs during both check-only buttons
  too, so an already-converted MP3 library can be backfilled without
  reconverting it. Picking the right release type required a second
  AcoustID lookup (the API only honors one `meta` mode per request) and
  matching against the file's own tagged album name, since blindly
  preferring a "Compilation" release group mislabeled most well-known
  songs (they almost always have one, even when tagged from the original
  studio album).
- **Import to Lidarr** now opens a live "Lidarr import" log window that
  streams every step as it happens - artist resolution, stale-trackfile
  cleanup, each scanned/submitted batch, and Lidarr's own command status
  (queued/started/completed) - instead of the UI going silent for the
  whole import. `lidarr.import_folder()` and friends take an optional
  `on_progress` callback for this.

### Changed
- Renamed the program from flac2mp3 to AcoustID: `flac2mp3.py` is now
  `acoustid.py`, the window title and the `QSettings` org/app identifiers
  changed to "AcoustID" (previously saved settings under the old
  "flac2mp3" identifier are not carried over), and the conversion log
  prefix changed from `flac2mp3-` to `acoustid-convert-`.

### Added
- New **Collection Summary** button (next to Browse): scans the current
  folder recursively, read-only, and shows a breakdown of the collection
  by release type (Album, EP, Single, Compilation, Promo, ...) - a
  horizontal bar chart (most common first) plus a total release count -
  in its own window. Chosen over a pie chart since it reads better with
  more than a handful of categories and a long tail of small ones. New
  `library_stats.py` module reads the `releasetype`/"MusicBrainz Album
  Type" tag from one file per directory (release type is an album-level
  property); untagged releases are counted as `Unknown`. Uses
  `PySide6.QtCharts`, already bundled with the existing `PySide6`
  dependency - no new package required. Runs in the background so
  scanning a large library doesn't freeze the window.
- All Lidarr API calls now retry automatically (`RETRY_ATTEMPTS`, with
  backoff) on connection errors, timeouts, and 5xx server errors, via a
  new `_with_retry()` wrapper - prompted by a real `Failed to delete
  stale track file: 404 Client Error` during a network hiccup. 4xx
  responses are never retried (logical/permanent, not transient), with
  one exception: deleting a stale TrackFile now treats a 404 as success
  rather than an error, since it means the record is already gone (most
  likely Lidarr's own concurrent reconciliation, or another run of this
  tool, got there first) - exactly the state that deletion wanted anyway.
- Diagnosed live (an Eisbrecher compilation, "Eiskalt", showed up as 16
  unmatched files with a "Couldn't find similar album" rejection): the
  album was a real MusicBrainz release, but the artist's Lidarr metadata
  profile had `Compilation` disabled, so it was never synced as a known
  album to match against - not something a stale-trackfile or path-
  mapping fix could touch. `explain_missing_album()` now detects this
  automatically: on that specific rejection, it looks the album up via
  Lidarr's own MusicBrainz-backed search and compares its type (both
  primary - Album/EP/Single/Broadcast/Other - and secondary -
  Compilation/Live/Remix/etc. - since Lidarr filters on both
  independently) against what the artist's metadata profile allows,
  replacing the generic rejection with a specific explanation (and what
  to do about it) whenever it finds one. Cached per album folder so a
  whole skipped album's tracks only trigger the lookup once.
- New **Auto-import to Lidarr after conversion** checkbox: when Start
  finishes converting at least one file, automatically runs the same
  Lidarr import as the existing button - no extra click needed. Off by
  default; only fires after a real conversion, and silently does nothing
  if the Lidarr URL/API key aren't configured yet (rather than an error
  dialog for a checkbox left on before setup was finished).

### Fixed
- The 500 error from a stale TrackFile record (see the entry below) could
  still happen even with `artistId` resolved, because clearing was only
  reactive - it ran after seeing a rejection, but Lidarr's scan can crash
  outright (a 500, its `AugmentingService` throwing trying to read a
  missing file) *before* returning any rejection data to react to.
  `import_folder()` now clears the resolved artist's stale TrackFile
  records proactively, before scanning at all
  (`clear_stale_trackfiles_for_artist()`), so the crash is avoided rather
  than reacted to. Verified against the real instance: a folder that
  previously 500'd now scans cleanly.
- `wait_for_command()` used one timeout for the whole wait, so a
  submission that was still validly *queued* behind other, unrelated
  Lidarr work (e.g. a large library rescan working through thousands of
  tracks) could time out with a misleading error even though nothing was
  actually stuck. It now has two separate budgets: a generous one
  (`COMMAND_QUEUE_TIMEOUT`, 30 min) for merely being queued, and the
  original tighter one for once Lidarr actually starts running it.
- Diagnosed against a real Lidarr instance: 184 files were reported as
  completely unmatched ("no artist match") despite having valid tags
  (confirmed independently via this tool's own AcoustID check). Lidarr's
  log showed why: `MultipleArtistsFoundException` - the library had two
  different artists with the same name, and unable to resolve which one
  a >100-file folder belonged to by name alone, Lidarr skipped reading
  tags for the entire folder rather than guessing, so every file came
  back with empty `audioTags` and no match at all. `import_folder()` now
  looks up the artist by matching the folder's path against Lidarr's own
  artist list first (new `get_artist_id_for_path()`) and passes that
  `artistId` with the scan, so Lidarr never needs to guess from an
  ambiguous name. Verified against the real instance: the same 184-file
  scan went from 0 matched/184 unmatched to 184/184 matched.
- A large import (e.g. importing a ~100-track discography in one go
  after a big conversion batch) submitted every matched file in a single
  ManualImport command and cleared stale TrackFile records with an
  unpaced burst of DELETE calls - both of which could overwhelm or time
  out against a real Lidarr instance. Large batches are now submitted in
  chunks of `IMPORT_BATCH_SIZE` (20) files with a short pause between
  chunks, and stale-trackfile deletions are similarly paced out.
- The manual-import folder scan used the same 30s timeout as quick
  metadata calls, but it does real per-file work server-side (reading
  embedded tags, matching releases) - a large folder or a busy Lidarr
  instance could genuinely take longer than that and fail with a plain
  "HTTP timed out" error. It now gets its own 180s timeout
  (`MANUAL_IMPORT_SCAN_TIMEOUT`), and a timeout specifically now explains
  what happened instead of a generic connection-error message.
- **Important**: `clear_stale_trackfiles()` deleted *every* TrackFile
  record for an album whose scan hit an "already has file" rejection,
  not just the genuinely stale ones. `DELETE /api/v1/trackfile` removes
  the actual file on disk, not just the database row - so on a real
  Lidarr instance this deleted a valid, just-imported MP3 alongside the
  actually-stale records for files this tool had deleted during
  conversion. It now only deletes a record after confirming, via a real
  filesystem check (honoring the local/Lidarr path mapping), that its
  file is genuinely gone - and requires being able to see the file's
  containing folder at all, so a misconfigured/cross-host path mapping
  can never make it wrongly conclude every file is "missing". When in
  doubt, a record is now left alone rather than deleted: a leftover stale
  record is recoverable on a later run; a wrongly deleted file is not.
- `submit_manual_import()` was forwarding Lidarr's raw manual-import scan
  result (which nests `artist`/`album`/`tracks` as full objects, for
  display) straight into the `POST /api/v1/command` ManualImport payload.
  That endpoint actually expects flat `artistId`/`albumId`/`trackIds`
  fields, so every import was silently submitted with `artistId: 0,
  albumId: 0` and the command never progressed past "queued". Found by
  testing against a real Lidarr instance. `lidarr.py` now builds the
  correct flat payload from each matched candidate.

### Added
- New **Local path to library**/**Same path inside Lidarr** fields in
  Lidarr Settings...: when this app sees the library at a different
  filesystem path than Lidarr does (e.g. Lidarr runs in a container or on
  another host and the same share is mounted differently here), Import to
  Lidarr and the Custom Script hook now rewrite the folder path to
  Lidarr's own view (`lidarr.remap_path_to_lidarr()`) before scanning -
  previously this silently found nothing, since Lidarr's manual-import
  API can only look things up under its own filesystem view. Leave both
  blank if paths already agree.
- New **Import to Lidarr** button (optional, off by default, entirely
  independent of everything else in the window): hands the current folder
  to Lidarr's own Manual Import API instead of writing to Lidarr's
  database directly or requiring its import UI. New `lidarr.py` module
  wraps the API: scan a folder for matches, submit the fully-matched
  ones, poll until the import command finishes. Files Lidarr can't
  auto-match from embedded tags are left untouched and reported back
  together with Lidarr's own rejection reason (e.g. "already has file"),
  so a surprising "0 imported" is diagnosable instead of a dead end. When
  a match is specifically rejected because Lidarr's database still has a
  TrackFile record for a file this tool already replaced (e.g. converted
  a FLAC to MP3 and deleted the original), that stale record is deleted
  via the API and the scan retried once - the same fix the proven
  TheCaptain989/lidarr-flac2mp3 script uses - rather than a blanket
  library rescan.
- New **Lidarr Settings...** dialog holding the Lidarr URL/API key (moved
  out of the main window), with a **Test Connection** button that
  verifies the host/port is reachable and the API key is accepted before
  you rely on them for an import.
- New headless mode (`lidarr_hook.py`): register `acoustid.py` itself as
  a Lidarr **Custom Script** (Settings > Connect) to have it convert new
  downloads to MP3 and hand them to Lidarr's Manual Import API
  automatically, with no GUI involved - the same wiring
  TheCaptain989/lidarr-flac2mp3 uses (`lidarr_*` environment variables),
  reusing the Lidarr URL/API key and quality preset already saved via the
  GUI. `main()` detects this invocation (via `lidarr_eventtype` in the
  environment) and dispatches to it before creating any Qt objects.
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
